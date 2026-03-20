"""Task Planner Agent - Plans and breaks down tasks into steps."""

def execute(inputs: dict) -> dict:
    """Execute the task planner agent."""
    task = inputs.get("task", "")
    
    # Simple task decomposition
    words = task.split()
    
    plan = {
        "task": task,
        "steps": [
            {"step": 1, "action": "Analyze task requirements", "status": "pending"},
            {"step": 2, "action": "Identify dependencies", "status": "pending"},
            {"step": 3, "action": "Execute main task", "status": "pending"},
            {"step": 4, "action": "Verify results", "status": "pending"},
        ],
        "estimated_complexity": "medium" if len(words) > 5 else "low",
        "keywords": words[:5]
    }
    
    return {
        "plan": plan,
        "status": "success"
    }

if __name__ == "__main__":
    result = execute({"task": "Build a web application with user authentication"})
    print(result)
