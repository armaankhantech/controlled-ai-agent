import json
from ollama import chat


# --------------------------------
# MOCK DATABASE
# --------------------------------

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


# --------------------------------
# SUPPORT TICKETS DATABASE
# --------------------------------

tickets = []


# --------------------------------
# TOOL 1 — GET ORDER STATUS
# --------------------------------

def get_order_status(order_id: str) -> dict:
    """Get the status and delivery date of an order."""

    if order_id not in orders:
        return {
            "error": "ORDER_NOT_FOUND",
            "order_id": order_id
        }

    return {
        "order_id": order_id,
        **orders[order_id]
    }


# --------------------------------
# TOOL 2 — GET CUSTOMER DETAILS
# --------------------------------

def get_customer_details(customer_id: str) -> dict:
    """Get customer information using a customer ID."""

    if customer_id not in customers:
        return {
            "error": "CUSTOMER_NOT_FOUND",
            "customer_id": customer_id
        }

    return {
        "customer_id": customer_id,
        **customers[customer_id]
    }


# --------------------------------
# TOOL 3 — CREATE SUPPORT TICKET
# --------------------------------

def create_support_ticket(
    customer_id: str,
    issue: str
) -> dict:
    """Create a support ticket for a customer."""

    # Validate customer ID
    if not customer_id:
        return {
            "error": "CUSTOMER_ID_REQUIRED",
            "message": "A customer ID is required to create a support ticket."
        }

    # Validate customer exists
    if customer_id not in customers:
        return {
            "error": "CUSTOMER_NOT_FOUND",
            "customer_id": customer_id
        }

    # Validate issue
    if not issue:
        return {
            "error": "ISSUE_REQUIRED",
            "message": "An issue description is required."
        }

    ticket_id = f"TICKET-{len(tickets) + 1001}"

    ticket = {
        "ticket_id": ticket_id,
        "customer_id": customer_id,
        "issue": issue,
        "status": "open"
    }

    tickets.append(ticket)

    return ticket


# --------------------------------
# TOOL 4 — SIMULATED DELETE CUSTOMER
# --------------------------------

def delete_customer(customer_id: str) -> dict:
    """
    Simulate deleting a customer.

    This tool represents a high-risk action.
    It will only execute after human approval.
    """

    if customer_id not in customers:
        return {
            "error": "CUSTOMER_NOT_FOUND",
            "customer_id": customer_id
        }

    return {
        "customer_id": customer_id,
        "status": "deleted",
        "message": "Customer deletion was simulated successfully."
    }


# --------------------------------
# TOOL REGISTRY
# --------------------------------

tool_registry = {
    "get_order_status": get_order_status,
    "get_customer_details": get_customer_details,
    "create_support_ticket": create_support_ticket,
    "delete_customer": delete_customer
}


# --------------------------------
# ALLOWED TOOLS
# --------------------------------

allowed_tools = {
    "get_order_status",
    "get_customer_details",
    "create_support_ticket",
    "delete_customer"
}


# --------------------------------
# TOOLS THAT REQUIRE HUMAN APPROVAL
# --------------------------------

approval_required_tools = {
    "delete_customer"
}


# --------------------------------
# TOOLS GIVEN TO THE LLM
# --------------------------------

available_tools = [
    get_order_status,
    get_customer_details,
    create_support_ticket,
    delete_customer
]


# --------------------------------
# AGENT LIMIT
# --------------------------------

MAX_STEPS = 5


# --------------------------------
# CONVERSATION
# --------------------------------

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
"""
    },
    {
        "role": "user",
        "content": "My order ORD-1002 is delayed. Check the order status and create a support ticket for customer C101."
    }
]


# --------------------------------
# AGENT LOOP
# --------------------------------

for step in range(1, MAX_STEPS + 1):

    print(f"\n=== AGENT STEP {step} ===")


    # --------------------------------
    # CALL LLM
    # --------------------------------

    response = chat(
        model="llama3.2:3b",
        messages=messages,
        tools=available_tools
    )


    print("\n=== LLM RESPONSE ===")
    print(response.message)


    # --------------------------------
    # CHECK FOR TOOL CALL
    # --------------------------------

    if not response.message.tool_calls:

        print("\n=== FINAL AI ANSWER ===")

        if response.message.content:
            print(response.message.content)
        else:
            print(
                "I don't have enough information to provide an answer."
            )

        break


    # --------------------------------
    # ADD LLM RESPONSE TO CONVERSATION
    # --------------------------------

    messages.append(response.message)


    # --------------------------------
    # EXECUTE ALL TOOL CALLS
    # --------------------------------

    for tool_call in response.message.tool_calls:

        tool_name = tool_call.function.name
        arguments = tool_call.function.arguments


        print("\n=== TOOL SELECTED ===")
        print(tool_name)


        print("\n=== ARGUMENTS ===")
        print(arguments)


        # --------------------------------
        # CHECK TOOL ALLOWLIST
        # --------------------------------

        if tool_name not in allowed_tools:

            result = {
                "error": "TOOL_NOT_ALLOWED",
                "message": f"Tool '{tool_name}' is not allowed."
            }

            print("\n=== TOOL BLOCKED ===")
            print(result)


        # --------------------------------
        # CHECK HUMAN APPROVAL
        # --------------------------------

        elif tool_name in approval_required_tools:

            print("\n=== HUMAN APPROVAL REQUIRED ===")

            print(
                f"The AI wants to execute: {tool_name}"
            )

            print(
                f"Arguments: {arguments}"
            )

            approval = input(
                "\nDo you approve this action? (yes/no): "
            )


            if approval.lower() == "yes":

                print("\n=== HUMAN APPROVED ===")

                tool_function = tool_registry.get(tool_name)

                if tool_function is None:

                    result = {
                        "error": "UNKNOWN_TOOL",
                        "message": f"Tool '{tool_name}' is not available."
                    }

                else:

                    result = tool_function(**arguments)


            else:

                print("\n=== HUMAN DENIED ===")

                result = {
                    "error": "ACTION_DENIED",
                    "message": "The human operator denied this action."
                }


        # --------------------------------
        # NORMAL TOOL
        # --------------------------------

        else:

            tool_function = tool_registry.get(tool_name)


            if tool_function is None:

                result = {
                    "error": "UNKNOWN_TOOL",
                    "message": f"Tool '{tool_name}' is not available."
                }

            else:

                result = tool_function(**arguments)


        # --------------------------------
        # DISPLAY TOOL RESULT
        # --------------------------------

        print("\n=== TOOL RESULT ===")
        print(result)


        # --------------------------------
        # SEND TOOL RESULT BACK TO LLM
        # --------------------------------

        messages.append({
            "role": "tool",
            "content": json.dumps(result)
        })


# --------------------------------
# MAX STEP PROTECTION
# --------------------------------

else:

    print("\n=== AGENT STOPPED ===")

    print(
        f"The agent reached the maximum limit of {MAX_STEPS} steps."
    )