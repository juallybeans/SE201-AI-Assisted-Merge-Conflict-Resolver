# Sample conflicts whose branches already contain syntax errors, for
# trying out the syntax repair attempt in the CLI:
#   python -m src.cli examples/repair_conflict.py
# A model that merges conservatively may copy the error into its first
# resolution, which fails the syntax check and triggers one repair.
# The model may also fix the error on its first try, so a repair is
# likely but not guaranteed for every conflict.


# Both sides are missing the colon after the function signature.
<<<<<<< HEAD
def total(items)
    return sum(items)
=======
def total(items)
    return sum(item.price for item in items)
>>>>>>> feature/prices


# Both sides leave the list comprehension bracket unclosed.
<<<<<<< HEAD
def tags(post):
    return [tag.lower() for tag in post.tags
=======
def tags(post):
    return [tag.strip() for tag in post.tags
>>>>>>> feature/clean-tags


# Only the incoming side is broken: its last line is mis-indented.
<<<<<<< HEAD
def is_admin(user):
    return user.role == "admin"
=======
def is_admin(user):
    if user.role == "admin":
        return True
      return user.is_superuser
>>>>>>> feature/superuser


# Both sides use Python 2 print statements.
<<<<<<< HEAD
def log_start(name):
    print "Starting", name
=======
def log_start(name, level):
    print "[%s] Starting %s" % (level, name)
>>>>>>> feature/log-level
