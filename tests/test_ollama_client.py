from src.resolver.ollama_client import resolve_conflict


conflict = """<<<<<<< HEAD
def greet(name):
    return f"Hello, {name}!"
=======
def greet(name):
    return f"Hi, {name}!"
>>>>>>> feature/greeting
"""

context = """
Current branch commit: Add personalised greeting.
Incoming branch commit: Change greeting wording.
"""

result = resolve_conflict(
    conflict=conflict,
    context=context,
)

print("=== Proposed resolution ===")
print(result["resolution"])

print("\n=== Explanation ===")
print(result["explanation"])

print("\n=== Assumptions and risks ===")
print(result["assumptions"])