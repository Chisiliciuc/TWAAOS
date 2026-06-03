import pytest
from app import app


@pytest.fixture
def client():
    # Setăm aplicația în modul de testare
    app.config['TESTING'] = True
    app.config['PRESERVE_CONTEXT_ON_EXCEPTION'] = False

    # Creăm clientul de testare
    with app.test_client() as client:
        with app.app_context():
            yield client


def test_homepage_status(client):
    """Verifică dacă pagina principală se încarcă corect (Status 200)"""
    res = client.get('/')
    assert res.status_code == 200


def test_api_events_get(client):
    """Verifică dacă API-ul de evenimente returnează o listă validă"""
    res = client.get('/api/events')
    assert res.status_code == 200
    assert isinstance(res.get_json(), list)


def test_login_invalid_credentials(client):
    """Verifică respingerea unui login greșit cu mesajul 'Date greșite'"""
    res = client.post('/login', json={
        "email": "wrong@student.usv.ro",
        "password": "wrongpassword"
    })

    # Verificăm codul de status 401 Unauthorized
    assert res.status_code == 401
    assert b"Date gre" in res.data