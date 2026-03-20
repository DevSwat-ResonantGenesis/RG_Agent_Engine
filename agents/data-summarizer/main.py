"""Data Summarizer Agent - Summarizes data and generates insights."""

def execute(inputs: dict) -> dict:
    """Execute the data summarizer agent."""
    data = inputs.get("data", {})
    
    summary = {
        "type": type(data).__name__,
        "keys": list(data.keys()) if isinstance(data, dict) else None,
        "length": len(data) if hasattr(data, "__len__") else None,
        "insights": []
    }
    
    if isinstance(data, dict):
        summary["insights"].append(f"Dictionary with {len(data)} keys")
        for key, value in data.items():
            summary["insights"].append(f"Key '{key}' has type {type(value).__name__}")
    elif isinstance(data, list):
        summary["insights"].append(f"List with {len(data)} items")
    
    return {
        "summary": summary,
        "status": "success"
    }

if __name__ == "__main__":
    result = execute({"data": {"name": "test", "value": 123}})
    print(result)
