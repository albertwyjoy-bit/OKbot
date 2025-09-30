from abc import abstractmethod


class BaseApp:
    @abstractmethod
    def run(self, command: str | None = None) -> bool:
        """
        Run the app with the given command.

        Args:
            command (str | None): The command to run.

        Returns:
            bool: Whether the run is successful.
        """
        ...
