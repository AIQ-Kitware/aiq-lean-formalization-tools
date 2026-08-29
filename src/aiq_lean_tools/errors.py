class FormalizationToolsError(RuntimeError):
    """Base error for user-facing tool failures."""


class ValidationError(FormalizationToolsError):
    """Raised when a tracked artifact fails structural validation."""


class LeanExecutionError(FormalizationToolsError):
    """Raised when an external Lean/Lake command cannot complete."""
