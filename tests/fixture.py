"""In-repo fixture for the smoke test (a definition for show_defun to serve)."""


def add(a, b):
    return a + b


class Box:
    def collect(self):
        return [1, 2, 3]
