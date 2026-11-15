"""The one rule for telling missing data from a programming error (shared by the report and the decision service)."""

from coding.service.on_chain.menthorq_input_loader import MissingIndexPriceError


def is_missing_data(error: Exception) -> bool:
    """
    True for the errors the services raise when data is missing (a plain ``LookupError``, or ``MissingIndexPriceError``).
    ``KeyError`` and ``IndexError`` are lookup errors too, but they are programming errors, not missing data.
    """
    return type(error) is LookupError or isinstance(error, MissingIndexPriceError)
