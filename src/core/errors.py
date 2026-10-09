"""Expected application failures; programming errors keep their original traceback."""


class ApplicationError(Exception):
    pass


class NotFoundError(ApplicationError):
    pass


class ConflictError(ApplicationError):
    pass


class InvalidSearchError(ApplicationError):
    pass


class DatabaseVersionError(RuntimeError):
    pass
