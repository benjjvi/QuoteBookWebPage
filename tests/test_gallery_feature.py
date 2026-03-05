import io


def _tiny_gif_bytes() -> bytes:
    return (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
        b"\xf9\x04\x01\n\x00\x01\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
        b"\x00\x02\x02L\x01\x00;"
    )


def test_gallery_upload_links_to_quote(client, csrf_token_for):
    csrf = csrf_token_for("/gallery/add")
    response = client.post(
        "/gallery/add",
        data={
            "csrf_token": csrf,
            "submitter_name": "Ben",
            "subjects": "Alice",
            "image_context": "Gallery upload test",
            "quote_id": "1",
            "image_file": (io.BytesIO(_tiny_gif_bytes()), "camera-roll.gif"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/gallery/" in response.headers["Location"]

    quote_page = client.get("/quote/1")
    assert quote_page.status_code == 200
    assert b"1 image linked to this quote." in quote_page.data
    assert b'alt="First test quote."' in quote_page.data

    all_quotes = client.get("/all_quotes")
    assert all_quotes.status_code == 200
    assert b"1 image linked to this quote." in all_quotes.data


def test_add_quote_with_image_upload_auto_links_gallery(client, csrf_token_for, quote_store):
    csrf = csrf_token_for("/add_quote")
    response = client.post(
        "/add_quote",
        data={
            "csrf_token": csrf,
            "quote_text": "Quote submitted with image",
            "author_info": "Alice",
            "context": "Added from web form",
            "tags": "photo",
            "quote_submitter_name": "Web form submitter",
            "quote_image_subjects": "Alice",
            "quote_image_context": "Camera roll upload",
            "quote_image_file": (io.BytesIO(_tiny_gif_bytes()), "with-quote.gif"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302

    newest = quote_store.get_latest_quote()
    assert newest is not None

    detail = client.get(f"/quote/{newest.id}")
    assert detail.status_code == 200
    assert b"1 image linked to this quote." in detail.data

    gallery = client.get("/gallery")
    assert gallery.status_code == 200
    assert b"Submitted by Web form submitter" in gallery.data


def test_api_add_quote_supports_multipart_image_upload(client):
    response = client.post(
        "/api/quotes",
        data={
            "quote": "API quote with image",
            "authors": "Alice",
            "context": "API multipart",
            "submitter_name": "API submitter",
            "subjects": "Alice",
            "image_context": "Uploaded via API",
            "image_file": (io.BytesIO(_tiny_gif_bytes()), "api-upload.gif"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["image_uploaded"] is True
    assert payload["linked_image_id"] > 0
    assert payload["image_count"] == 1


def test_gallery_subject_search_and_social_avatar_source(client, csrf_token_for):
    csrf = csrf_token_for("/gallery/add")
    upload = client.post(
        "/gallery/add",
        data={
            "csrf_token": csrf,
            "submitter_name": "Camera owner",
            "subjects": "Alice",
            "image_context": "Profile source",
            "quote_id": "",
            "image_file": (io.BytesIO(_tiny_gif_bytes()), "alice-profile.gif"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert upload.status_code == 302

    filtered = client.get("/gallery", query_string={"subject": "Alice"})
    assert filtered.status_code == 200
    assert b"matching subject" in filtered.data

    social = client.get("/social")
    assert social.status_code == 200
    assert b"subjectAvatarMap" in social.data
    assert b"alice" in social.data
    assert b"/static/uploads/gallery/" in social.data
