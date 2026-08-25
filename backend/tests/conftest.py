from fastapi.testclient import TestClient


_original_init = TestClient.__init__


def _authenticated_init(self: TestClient, *args: object, **kwargs: object) -> None:
    """Test klient používá skutečný ADMIN bearer credential, nikoli auth bypass."""
    _original_init(self, *args, **kwargs)  # type: ignore[arg-type]
    from quantlab import api

    self.headers["Authorization"] = f"Bearer {api.settings.api_admin_token}"


TestClient.__init__ = _authenticated_init  # type: ignore[method-assign]
