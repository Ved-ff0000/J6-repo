class WorkflowFailure(Exception):
    """Fixed safe code only; never include document text or upstream errors."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
