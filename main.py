import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from ollama import chat


# ============================================================
# CUSTOM ERRORS
# ============================================================

class ToolError(Exception):
    """Base error for all tool-related failures."""


class ValidationError(ToolError):
    """Raised when tool arguments are invalid."""


class ToolNotFoundError(ToolError):
    """Raised when a requested tool does not exist."""


class ToolTimeoutError(ToolError):
    """Raised when a tool takes too long."""


class ToolExecutionError(ToolError):
    """Raised when a tool fails during execution."""


class TransientError(ToolError):
    """Raised when a temporary failure may succeed after retrying."""


# ============================================================
# MOCK DATABASE
# ============================================================

orders = {
    "ORD-1001": {
        "status": "processing",
        "delivery": "2026-09-25"
    },
    "ORD-1002": {
        "status": "shipped",
        "delivery": "2026-09-23"
    },
    "ORD-1003": {
        "status": "delivered",
        "delivery": "2026-09-20"
    }
}


customers = {
    "C101": {
        "name": "Rahul Sharma",
        "email": "rahul@example.com"
    },
    "C102": {
        "name": "Priya Mehta",
        "email": "priya@example.com"
    },
    "C103": {
        "name": "Arjun Patel",
        "email": "arjun@example.com"
    }
}


# ============================================================
# SUPPORT TICKETS DATABASE
# ============================================================

tickets = []


# ============================================================
# IDEMPOTENCY STORE
# ============================================================

processed_requests = {}


# ============================================================
# TOOL 1 — GET ORDER STATUS
# ============================================================

def get_order_status(order_id: str) -> dict:
    """Get the status and delivery date of an order."""

    if not order_id:
        raise ValidationError("order_id is required.")

    if order_id not in orders:
        raise ToolExecutionError(
            f"Order '{order_id}' does not exist."
        )

    return {
        "order_id": order_id,
        **orders[order_id]
    }


# ============================================================
# TOOL 2 — GET CUSTOMER DETAILS
# ============================================================

def get_customer_details(customer_id: str) -> dict:
    """Get customer information using a customer ID."""

    if not customer_id:
        raise ValidationError("customer_id is required.")

    if customer_id not in customers:
        raise ToolExecutionError(
            f"Customer '{customer_id}' does not exist."
        )

    return {
        "customer_id": customer_id,
        **customers[customer_id]
    }


# ============================================================
# TOOL 3 — CREATE SUPPORT TICKET
# ============================================================

def create_support_ticket(
    customer_id: str,
    issue: str
) -> dict:
    """Create a support ticket for a customer."""

    if not customer_id:
        raise ValidationError(
            "customer_id is required."
        )

    if customer_id not in customers:
        raise ToolExecutionError(
            f"Customer '{customer_id}' does not exist."
        )

    if not issue:
        raise ValidationError(
            "issue is required."
        )

    ticket_id = f"TICKET-{len(tickets) + 1001}"

    ticket = {
        "ticket_id": ticket_id,
        "customer_id": customer_id,
        "issue": issue,
        "status": "open"
    }

    tickets.append(ticket)

    return ticket


# ============================================================
# TOOL 4 — SIMULATED DELETE CUSTOMER
# ============================================================

def delete_customer(customer_id: str) -> dict:
    """
    Simulate deleting a customer.

    This is a high-risk action and requires
    human approval before execution.
    """

    if not customer_id:
        raise ValidationError(
            "customer_id is required."
        )

    if customer_id not in customers:
        raise ToolExecutionError(
            f"Customer '{customer_id}' does not exist."
        )

    return {
        "customer_id": customer_id,
        "status": "deleted",
        "message": "Customer deletion was simulated successfully."
    }


# ============================================================
# TOOL REGISTRY
# ============================================================

tool_registry = {
    "get_order_status": get_order_status,
    "get_customer_details": get_customer_details,
    "create_support_ticket": create_support_ticket,
    "delete_customer": delete_customer
}


# ============================================================
# ALLOWED TOOLS
# ============================================================

allowed_tools = {
    "get_order_status",
    "get_customer_details",
    "create_support_ticket",
    "delete_customer"
}


# ============================================================
# TOOLS REQUIRING HUMAN APPROVAL
# ============================================================

approval_required_tools = {
    "delete_customer"
}


# ============================================================
# TOOLS PROVIDED TO THE LLM
# ============================================================

available_tools = [
    get_order_status,
    get_customer_details,
    create_support_ticket,
    delete_customer
]


# ============================================================
# AGENT SAFETY LIMIT
# ============================================================

MAX_STEPS = 5


# ============================================================
# RELIABILITY SETTINGS
# ============================================================

MAX_RETRIES = 3
TOOL_TIMEOUT = 5
BACKOFF_BASE = 1


# ============================================================
# STRUCTURED LOGGING
# ============================================================

def log_event(
    request_id: str,
    event: str,
    details=None
):
    """
    Print a structured log message.

    request_id identifies the complete agent request.
    event describes what happened.
    details contains optional additional information.
    """

    message = f"[{request_id}] {event}"

    if details is not None:
        message += f" | {details}"

    print(message)


# ============================================================
# EXPONENTIAL BACKOFF
# ============================================================

def calculate_backoff(attempt: int) -> int:
    """
    Calculate exponential backoff.

    attempt 0 -> 1 second
    attempt 1 -> 2 seconds
    attempt 2 -> 4 seconds
    """

    return BACKOFF_BASE * (2 ** attempt)


# ============================================================
# ARGUMENT VALIDATION
# ============================================================

def validate_arguments(
    tool_name: str,
    arguments: dict
):
    """Validate tool arguments before execution."""

    if not isinstance(arguments, dict):
        raise ValidationError(
            "Tool arguments must be provided as a dictionary."
        )

    if tool_name == "get_order_status":

        if not arguments.get("order_id"):
            raise ValidationError(
                "order_id is required."
            )

    elif tool_name == "get_customer_details":

        if not arguments.get("customer_id"):
            raise ValidationError(
                "customer_id is required."
            )

    elif tool_name == "create_support_ticket":

        if not arguments.get("customer_id"):
            raise ValidationError(
                "customer_id is required."
            )

        if not arguments.get("issue"):
            raise ValidationError(
                "issue is required."
            )

    elif tool_name == "delete_customer":

        if not arguments.get("customer_id"):
            raise ValidationError(
                "customer_id is required."
            )


# ============================================================
# SAFE TOOL EXECUTION
# ============================================================

def execute_tool_safely(
    tool_name: str,
    arguments: dict,
    request_id: str
) -> dict:
    """
    Execute a tool with validation and timeout protection.
    """

    validate_arguments(
        tool_name,
        arguments
    )

    tool_function = tool_registry.get(tool_name)

    if tool_function is None:
        raise ToolNotFoundError(
            f"Tool '{tool_name}' does not exist."
        )

    executor = ThreadPoolExecutor(
        max_workers=1
    )

    future = executor.submit(
        tool_function,
        **arguments
    )

    try:

        result = future.result(
            timeout=TOOL_TIMEOUT
        )

        if not isinstance(result, dict):
            raise ToolExecutionError(
                "Tool returned an invalid result."
            )

        return result

    except FuturesTimeoutError:

        future.cancel()

        raise ToolTimeoutError(
            f"Tool '{tool_name}' exceeded "
            f"{TOOL_TIMEOUT} seconds."
        )

    except ToolError:

        raise

    except Exception as error:

        raise ToolExecutionError(
            str(error)
        ) from error

    finally:

        # Do not wait for a timed-out task.
        executor.shutdown(
            wait=False,
            cancel_futures=True
        )


# ============================================================
# RETRY WRAPPER
# ============================================================

def execute_with_retries(
    tool_name: str,
    arguments: dict,
    request_id: str
) -> dict:
    """
    Execute a tool with controlled retries.

    Only transient errors and timeouts are retried.
    Validation and permanent errors are not retried.
    """

    for attempt in range(MAX_RETRIES + 1):

        try:

            log_event(
                request_id,
                "TOOL_ATTEMPT",
                f"{tool_name} | attempt={attempt + 1}"
            )

            result = execute_tool_safely(
                tool_name,
                arguments,
                request_id
            )

            return result

        except TransientError as error:

            if attempt == MAX_RETRIES:

                log_event(
                    request_id,
                    "RETRY_LIMIT_REACHED",
                    str(error)
                )

                raise

            delay = calculate_backoff(
                attempt
            )

            log_event(
                request_id,
                "RETRYING",
                f"delay={delay}s | error={error}"
            )

            time.sleep(delay)

        except ToolTimeoutError as error:

            if attempt == MAX_RETRIES:

                log_event(
                    request_id,
                    "TIMEOUT_RETRY_LIMIT",
                    str(error)
                )

                raise

            delay = calculate_backoff(
                attempt
            )

            log_event(
                request_id,
                "TIMEOUT_RETRY",
                f"delay={delay}s"
            )

            time.sleep(delay)

        except ToolError:

            # Permanent errors are NOT retried.
            raise


# ============================================================
# IDEMPOTENCY KEY
# ============================================================

def create_idempotency_key(
    tool_name: str,
    arguments: dict,
    idempotency_key: str | None = None
) -> str:
    """
    Create a stable idempotency key.

    If the caller provides an explicit idempotency key,
    use it.

    Otherwise create a deterministic key from the
    tool name and arguments.
    """

    if idempotency_key:
        return idempotency_key

    normalized_arguments = json.dumps(
        arguments,
        sort_keys=True
    )

    return (
        f"{tool_name}:"
        f"{normalized_arguments}"
    )


# ============================================================
# FALLBACK
# ============================================================

def fallback_response(
    request_id: str,
    tool_name: str,
    error: Exception
) -> dict:
    """
    Return a safe fallback when automation fails.
    """

    log_event(
        request_id,
        "FALLBACK_TRIGGERED",
        f"{tool_name} | {error}"
    )

    return {
        "error": "TOOL_FAILED",
        "status": "human_review_required",
        "tool": tool_name,
        "message": (
            "The requested action could not be completed "
            "automatically. The failure has been recorded "
            "for human review."
        )
    }


# ============================================================
# LLM FALLBACK
# ============================================================

def llm_fallback(
    request_id: str,
    error: Exception
) -> dict:
    """
    Safe response when the LLM itself is unavailable.
    """

    log_event(
        request_id,
        "LLM_FAILURE",
        str(error)
    )

    return {
        "error": "LLM_UNAVAILABLE",
        "status": "human_review_required",
        "message": (
            "The AI service is temporarily unavailable. "
            "The request should be reviewed or retried later."
        )
    }


# ============================================================
# LLM CALL
# ============================================================

def call_llm(
    messages: list,
    request_id: str
):
    """
    Call the local Ollama model.

    LLM failures are converted into a controlled
    ToolError so the main agent can handle them safely.
    """

    try:

        return chat(
            model="llama3.2:3b",
            messages=messages,
            tools=available_tools
        )

    except Exception as error:

        log_event(
            request_id,
            "LLM_FAILURE",
            str(error)
        )

        raise ToolExecutionError(
            f"LLM request failed: {error}"
        ) from error


# ============================================================
# AGENT
# ============================================================

def run_agent(
    messages: list
):
    """
    Run the controlled AI agent.
    """

    request_id = (
        f"REQ-{uuid.uuid4().hex[:8]}"
    )

    log_event(
        request_id,
        "REQUEST_STARTED"
    )

    try:

        for step in range(
            1,
            MAX_STEPS + 1
        ):

            print(
                f"\n=== AGENT STEP {step} ==="
            )

            log_event(
                request_id,
                "AGENT_STEP",
                step
            )

            # ------------------------------------------------
            # CALL LLM
            # ------------------------------------------------

            try:

                response = call_llm(
                    messages,
                    request_id
                )

            except ToolError as error:

                result = llm_fallback(
                    request_id,
                    error
                )

                print(
                    "\n=== LLM FALLBACK ==="
                )

                print(result)

                return result

            print(
                "\n=== LLM RESPONSE ==="
            )

            print(
                response.message
            )

            # ------------------------------------------------
            # NO TOOL CALL = FINAL ANSWER
            # ------------------------------------------------

            if not response.message.tool_calls:

                print(
                    "\n=== FINAL AI ANSWER ==="
                )

                if response.message.content:

                    print(
                        response.message.content
                    )

                else:

                    print(
                        "I don't have enough information "
                        "to provide an answer."
                    )

                log_event(
                    request_id,
                    "REQUEST_COMPLETED"
                )

                return response.message.content

            # ------------------------------------------------
            # ADD LLM RESPONSE
            # ------------------------------------------------

            messages.append(
                response.message
            )

            # ------------------------------------------------
            # EXECUTE TOOL CALLS
            # ------------------------------------------------

            for tool_call in response.message.tool_calls:

                tool_name = (
                    tool_call.function.name
                )

                arguments = (
                    tool_call.function.arguments
                )

                print(
                    "\n=== TOOL SELECTED ==="
                )

                print(
                    tool_name
                )

                print(
                    "\n=== ARGUMENTS ==="
                )

                print(
                    arguments
                )

                log_event(
                    request_id,
                    "TOOL_SELECTED",
                    tool_name
                )

                # ------------------------------------------------
                # ALLOWLIST
                # ------------------------------------------------

                if tool_name not in allowed_tools:

                    result = {
                        "error": "TOOL_NOT_ALLOWED",
                        "message": (
                            f"Tool '{tool_name}' "
                            "is not allowed."
                        )
                    }

                    log_event(
                        request_id,
                        "TOOL_BLOCKED",
                        tool_name
                    )

                # ------------------------------------------------
                # HUMAN APPROVAL
                # ------------------------------------------------

                elif tool_name in approval_required_tools:

                    print(
                        "\n=== HUMAN APPROVAL REQUIRED ==="
                    )

                    print(
                        f"The AI wants to execute: "
                        f"{tool_name}"
                    )

                    print(
                        f"Arguments: {arguments}"
                    )

                    approval = input(
                        "\nDo you approve this action? "
                        "(yes/no): "
                    )

                    if approval.lower() == "yes":

                        print(
                            "\n=== HUMAN APPROVED ==="
                        )

                        try:

                            result = execute_with_retries(
                                tool_name,
                                arguments,
                                request_id
                            )

                        except ToolError as error:

                            result = fallback_response(
                                request_id,
                                tool_name,
                                error
                            )

                    else:

                        print(
                            "\n=== HUMAN DENIED ==="
                        )

                        result = {
                            "error": "ACTION_DENIED",
                            "message": (
                                "The human operator "
                                "denied this action."
                            )
                        }

                # ------------------------------------------------
                # NORMAL TOOL
                # ------------------------------------------------

                else:

                    try:

                        idempotency_key = (
                            create_idempotency_key(
                                tool_name,
                                arguments
                            )
                        )

                        if (
                            idempotency_key
                            in processed_requests
                        ):

                            log_event(
                                request_id,
                                "DUPLICATE_ACTION",
                                tool_name
                            )

                            result = (
                                processed_requests[
                                    idempotency_key
                                ]
                            )

                        else:

                            result = (
                                execute_with_retries(
                                    tool_name,
                                    arguments,
                                    request_id
                                )
                            )

                            processed_requests[
                                idempotency_key
                            ] = result

                            log_event(
                                request_id,
                                "TOOL_SUCCESS",
                                tool_name
                            )

                    except ValidationError as error:

                        log_event(
                            request_id,
                            "VALIDATION_ERROR",
                            str(error)
                        )

                        result = {
                            "error": "VALIDATION_ERROR",
                            "message": str(error)
                        }

                    except ToolTimeoutError as error:

                        result = fallback_response(
                            request_id,
                            tool_name,
                            error
                        )

                    except ToolNotFoundError as error:

                        log_event(
                            request_id,
                            "TOOL_NOT_FOUND",
                            str(error)
                        )

                        result = {
                            "error": "TOOL_NOT_FOUND",
                            "message": str(error)
                        }

                    except ToolError as error:

                        result = fallback_response(
                            request_id,
                            tool_name,
                            error
                        )

                # ------------------------------------------------
                # TOOL RESULT
                # ------------------------------------------------

                print(
                    "\n=== TOOL RESULT ==="
                )

                print(
                    result
                )

                # ------------------------------------------------
                # SEND RESULT BACK TO LLM
                # ------------------------------------------------

                messages.append(
                    {
                        "role": "tool",
                        "content": json.dumps(result)
                    }
                )

        # --------------------------------------------------------
        # MAX STEPS REACHED
        # --------------------------------------------------------

        log_event(
            request_id,
            "MAX_STEPS_REACHED",
            f"limit={MAX_STEPS}"
        )

        result = {
            "error": "MAX_STEPS_REACHED",
            "status": "human_review_required",
            "message": (
                "The agent reached its maximum "
                "execution limit and stopped safely."
            )
        }

        print(
            "\n=== AGENT STOPPED SAFELY ==="
        )

        print(
            result
        )

        return result

    except Exception as error:

        log_event(
            request_id,
            "UNEXPECTED_AGENT_FAILURE",
            str(error)
        )

        result = {
            "error": "UNEXPECTED_AGENT_FAILURE",
            "status": "human_review_required",
            "message": (
                "An unexpected error occurred. "
                "The request has been stopped safely."
            )
        }

        print(
            "\n=== UNEXPECTED FAILURE ==="
        )

        print(
            result
        )

        return result


# ============================================================
# FAILURE TEST HELPERS
# ============================================================

def simulate_timeout():
    """
    Test tool that intentionally takes longer
    than the configured timeout.
    """

    print(
        "\n[TEST TOOL] Starting slow operation..."
    )

    time.sleep(
        TOOL_TIMEOUT + 2
    )

    return {
        "status": "success"
    }


def simulate_transient_failure(
    attempts: list
):
    """
    Simulate a temporary service failure.

    The tool fails twice and succeeds on the
    third attempt.
    """

    attempts[0] += 1

    print(
        f"\n[TEST TOOL] Attempt number: "
        f"{attempts[0]}"
    )

    if attempts[0] < 3:

        raise TransientError(
            "Simulated temporary service failure."
        )

    return {
        "status": "success",
        "message": (
            "Temporary failure recovered."
        )
    }


# ============================================================
# FAILURE TESTS
# ============================================================

def run_reliability_tests():

    print(
        "\n\n"
        "============================================================"
    )

    print(
        "DAY 55 — RELIABILITY TEST SUITE"
    )

    print(
        "============================================================"
    )

    # ========================================================
    # TEST 1 — INVALID ARGUMENT
    # ========================================================

    print(
        "\n=============================="
    )

    print(
        "TEST 1 — INVALID ARGUMENT"
    )

    print(
        "=============================="
    )

    test_request_id = (
        "TEST-VALIDATION-001"
    )

    try:

        execute_with_retries(
            "create_support_ticket",
            {
                "issue": "Missing customer ID"
            },
            test_request_id
        )

    except ValidationError as error:

        print(
            "\nVALIDATION ERROR CAUGHT:"
        )

        print(
            error
        )

    # ========================================================
    # TEST 2 — PERMANENT FAILURE
    # ========================================================

    print(
        "\n=============================="
    )

    print(
        "TEST 2 — ORDER NOT FOUND"
    )

    print(
        "=============================="
    )

    test_request_id = (
        "TEST-PERMANENT-001"
    )

    try:

        execute_with_retries(
            "get_order_status",
            {
                "order_id": "ORD-999999"
            },
            test_request_id
        )

    except ToolExecutionError as error:

        print(
            "\nPERMANENT ERROR CAUGHT:"
        )

        print(
            error
        )

    # ========================================================
    # TEST 3 — TIMEOUT
    # ========================================================

    print(
        "\n=============================="
    )

    print(
        "TEST 3 — TOOL TIMEOUT"
    )

    print(
        "=============================="
    )

    test_request_id = (
        "TEST-TIMEOUT-001"
    )

    original_tool = (
        tool_registry.get(
            "simulate_timeout"
        )
    )

    tool_registry[
        "simulate_timeout"
    ] = simulate_timeout

    try:

        execute_with_retries(
            "simulate_timeout",
            {},
            test_request_id
        )

    except ToolTimeoutError as error:

        print(
            "\nTIMEOUT ERROR CAUGHT:"
        )

        print(
            error
        )

    finally:

        if original_tool is None:

            tool_registry.pop(
                "simulate_timeout",
                None
            )

        else:

            tool_registry[
                "simulate_timeout"
            ] = original_tool

    # ========================================================
    # TEST 4 — TRANSIENT FAILURE
    # ========================================================

    print(
        "\n=============================="
    )

    print(
        "TEST 4 — TRANSIENT FAILURE"
    )

    print(
        "=============================="
    )

    test_request_id = (
        "TEST-TRANSIENT-001"
    )

    attempts = [0]

    original_tool = (
        tool_registry.get(
            "simulate_transient_failure"
        )
    )

    tool_registry[
        "simulate_transient_failure"
    ] = lambda: simulate_transient_failure(
        attempts
    )

    try:

        result = execute_with_retries(
            "simulate_transient_failure",
            {},
            test_request_id
        )

        print(
            "\nTEST RESULT:"
        )

        print(
            result
        )

    finally:

        if original_tool is None:

            tool_registry.pop(
                "simulate_transient_failure",
                None
            )

        else:

            tool_registry[
                "simulate_transient_failure"
            ] = original_tool

    # ========================================================
    # TEST 5 — IDEMPOTENCY
    # ========================================================

    print(
        "\n=============================="
    )

    print(
        "TEST 5 — IDEMPOTENCY"
    )

    print(
        "=============================="
    )

    test_request_id = (
        "TEST-IDEMPOTENCY-001"
    )

    test_arguments = {
        "customer_id": "C101",
        "issue": "Duplicate request test"
    }

    idempotency_key = (
        create_idempotency_key(
            "create_support_ticket",
            test_arguments
        )
    )

    print(
        "\nIDEMPOTENCY KEY:"
    )

    print(
        idempotency_key
    )

    # First request

    if idempotency_key not in processed_requests:

        first_result = (
            execute_with_retries(
                "create_support_ticket",
                test_arguments,
                test_request_id
            )
        )

        processed_requests[
            idempotency_key
        ] = first_result

    else:

        first_result = (
            processed_requests[
                idempotency_key
            ]
        )

    print(
        "\nFIRST RESULT:"
    )

    print(
        first_result
    )

    # Second request

    if idempotency_key in processed_requests:

        print(
            "\nDUPLICATE REQUEST DETECTED"
        )

        second_result = (
            processed_requests[
                idempotency_key
            ]
        )

    else:

        second_result = (
            execute_with_retries(
                "create_support_ticket",
                test_arguments,
                test_request_id
            )
        )

        processed_requests[
            idempotency_key
        ] = second_result

    print(
        "\nSECOND RESULT:"
    )

    print(
        second_result
    )

    print(
        "\nTOTAL TICKETS:"
    )

    print(
        len(tickets)
    )

    # ========================================================
    # TEST 6 — LLM FAILURE
    # ========================================================

    print(
        "\n=============================="
    )

    print(
        "TEST 6 — LLM FAILURE"
    )

    print(
        "=============================="
    )

    test_request_id = (
        "TEST-LLM-FAILURE-001"
    )

    simulated_llm_error = (
        ToolExecutionError(
            "Simulated LLM service unavailable."
        )
    )

    fallback = llm_fallback(
        test_request_id,
        simulated_llm_error
    )

    print(
        "\nLLM FALLBACK:"
    )

    print(
        fallback
    )

    # ========================================================
    # TEST 7 — MAX STEPS
    # ========================================================

    print(
        "\n=============================="
    )

    print(
        "TEST 7 — MAX STEPS"
    )

    print(
        "=============================="
    )

    test_request_id = (
        "TEST-MAX-STEPS-001"
    )

    test_max_steps = 3

    for step in range(
        1,
        test_max_steps + 1
    ):

        log_event(
            test_request_id,
            "AGENT_STEP",
            step
        )

    log_event(
        test_request_id,
        "MAX_STEPS_REACHED",
        f"limit={test_max_steps}"
    )

    print(
        "\nAgent stopped safely because "
        "the maximum step limit was reached."
    )

    # ========================================================
    # TEST 8 — FALLBACK
    # ========================================================

    print(
        "\n=============================="
    )

    print(
        "TEST 8 — FALLBACK / HUMAN REVIEW"
    )

    print(
        "=============================="
    )

    test_request_id = (
        "TEST-FALLBACK-001"
    )

    simulated_error = ToolExecutionError(
        "Simulated external service failure."
    )

    fallback = fallback_response(
        test_request_id,
        "create_support_ticket",
        simulated_error
    )

    print(
        "\nFALLBACK RESULT:"
    )

    print(
        fallback
    )

    print(
        "\n\n"
        "============================================================"
    )

    print(
        "ALL DAY 55 RELIABILITY TESTS COMPLETED"
    )

    print(
        "============================================================"
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    # --------------------------------------------------------
    # NORMAL AGENT DEMO
    # --------------------------------------------------------

    messages = [
        {
            "role": "system",
            "content": """
You are a customer support assistant.

You have access to tools that can retrieve customer information,
check orders, create support tickets, and request customer deletion.

When a tool returns information, treat that information as authoritative.

Do not invent information.

Never claim that an action was completed unless a tool result confirms
that the action was successfully completed.

If required information is missing, ask the user for it.

If none of the available tools can handle the user's request,
do not call an unrelated tool.

Only answer using information available in the conversation
or returned by a tool.

Do not invent dates, names, ticket assignments, follow-up actions,
or communication with other teams.

If a tool result does not contain a piece of information,
do not claim that information.
"""
        },
        {
            "role": "user",
            "content": (
                "My order ORD-1002 is delayed. "
                "Check the order status and create a support "
                "ticket for customer C101."
            )
        }
    ]

    run_agent(
        messages
    )

    # --------------------------------------------------------
    # RELIABILITY TEST SUITE
    # --------------------------------------------------------

    run_reliability_tests()