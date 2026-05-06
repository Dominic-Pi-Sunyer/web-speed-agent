"""Exception hierarchy for web-speed-agent."""


class WebSpeedError(Exception):
    """Base exception for all SDK errors."""


class AuthenticationError(WebSpeedError):
    """API key missing, invalid, or expired."""


class InsufficientCreditsError(WebSpeedError):
    """Account has no credits remaining."""


class APIError(WebSpeedError):
    """Web Speed API returned a non-2xx response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP {status_code}: {message}")


class RateLimitError(APIError):
    """API rate limit exceeded (HTTP 429)."""


class CredentialError(WebSpeedError):
    """Credential storage or retrieval failed."""


class BrowserError(WebSpeedError):
    """Playwright operation failed."""


class NetworkError(WebSpeedError):
    """Network request failed (timeout, DNS, etc.)."""


class PlaywrightNotInstalledError(BrowserError):
    """Playwright browsers not installed. Run: playwright install chromium"""
