"""JSON Validator Agent - Validates JSON data against schemas."""

import json

def execute(inputs: dict) -> dict:
    """Execute the JSON validator agent."""
    json_data = inputs.get("json_data", "")
    schema = inputs.get("schema", None)
    
    errors = []
    valid = True
    parsed = None
    
    # Try to parse JSON
    try:
        parsed = json.loads(json_data) if isinstance(json_data, str) else json_data
    except json.JSONDecodeError as e:
        valid = False
        errors.append(f"Invalid JSON: {str(e)}")
    
    # Basic schema validation if schema provided
    if valid and schema and parsed:
        required = schema.get("required", [])
        for field in required:
            if field not in parsed:
                valid = False
                errors.append(f"Missing required field: {field}")
    
    return {
        "valid": valid,
        "errors": errors,
        "parsed_type": type(parsed).__name__ if parsed else None,
        "status": "success"
    }

if __name__ == "__main__":
    result = execute({"json_data": '{"name": "test"}', "schema": {"required": ["name"]}})
    print(result)
