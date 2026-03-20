"""Hello World Agent - Simple test agent for decentralized node."""

def execute(inputs: dict) -> dict:
    """Execute the hello world agent."""
    message = inputs.get("message", "Hello")
    return {
        "response": f"{message}, World! From decentralized node.",
        "status": "success"
    }

if __name__ == "__main__":
    result = execute({"message": "Hello"})
    print(result)
