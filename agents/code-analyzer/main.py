"""Code Analyzer Agent - Analyzes code for patterns and issues."""

import re

def execute(inputs: dict) -> dict:
    """Execute the code analyzer agent."""
    code = inputs.get("code", "")
    language = inputs.get("language", "python")
    
    analysis = {
        "language": language,
        "lines": len(code.split("\n")),
        "characters": len(code),
        "functions": len(re.findall(r"def \w+", code)) if language == "python" else 0,
        "classes": len(re.findall(r"class \w+", code)) if language == "python" else 0,
        "imports": len(re.findall(r"^import |^from ", code, re.MULTILINE)),
        "issues": []
    }
    
    # Basic checks
    if "eval(" in code:
        analysis["issues"].append("Potential security issue: eval() usage detected")
    if "exec(" in code:
        analysis["issues"].append("Potential security issue: exec() usage detected")
    
    return {
        "analysis": analysis,
        "status": "success"
    }

if __name__ == "__main__":
    result = execute({"code": "def hello():\n    print('world')", "language": "python"})
    print(result)
