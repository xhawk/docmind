import io

import pytest

from docmind import storage


def upload(client, filename, data=b"hello"):
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def test_index_shows_upload_form(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b'type="file"' in response.data


def test_upload_stores_file_and_lists_it(app, client):
    response = upload(client, "scan.pdf")
    assert response.status_code == 302

    stored = list(app.config["UPLOAD_DIR"].iterdir())
    assert len(stored) == 1
    assert stored[0].name.endswith("-scan.pdf")
    assert stored[0].read_bytes() == b"hello"
    assert stored[0].name.encode() in client.get("/").data


def test_two_uploads_of_the_same_name_both_survive(app, client):
    upload(client, "scan.pdf")
    upload(client, "scan.pdf")
    assert len(list(app.config["UPLOAD_DIR"].iterdir())) == 2


def test_traversal_filename_stays_inside_upload_dir(app, client):
    upload(client, "../../evil.pdf")

    upload_dir = app.config["UPLOAD_DIR"]
    stored = list(upload_dir.iterdir())
    assert len(stored) == 1
    assert stored[0].name.endswith("-evil.pdf")
    assert stored[0].parent == upload_dir
    # Nothing escaped two levels up.
    assert not (upload_dir.parent.parent / "evil.pdf").exists()


def test_download_returns_uploaded_bytes(app, client):
    upload(client, "notes.txt", data=b"document body")
    name = next(iter(app.config["UPLOAD_DIR"].iterdir())).name

    response = client.get(f"/files/{name}")
    assert response.status_code == 200
    assert response.data == b"document body"


def test_download_of_unknown_file_is_404(client):
    assert client.get("/files/nope.pdf").status_code == 404


def test_upload_without_file_field_is_400(client):
    assert client.post("/upload", data={}).status_code == 400


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("report.pdf", "document"),
        ("scan.PNG", "image"),
        ("photo.jpeg", "image"),
        ("notes.txt", None),
        ("archive.tar.zst", None),
        ("no-extension", None),
    ],
)
def test_preview_kind_follows_the_extension(name, kind):
    assert storage.preview_kind(name) == kind
