"""Purchase outcomes. An unknown outcome is not a retryable failure."""


class PurchaseNotSent(Exception):
    pass


class PurchaseOutcomeUnknown(Exception):
    pass


class PurchaseRejected(Exception):
    pass


class DerivCallError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
