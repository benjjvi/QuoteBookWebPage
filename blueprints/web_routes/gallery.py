from flask import abort, redirect, render_template, request, url_for


def register_gallery_routes(bp, context):
    quote_store = context["quote_store"]
    services = context["services"]

    def _sorted_quotes_newest_first():
        return sorted(
            quote_store.get_all_quotes(),
            key=lambda item: (item.timestamp, item.id),
            reverse=True,
        )

    def _resolve_quotes(quote_ids):
        quotes = []
        for quote_id in services.parse_int_id_list(quote_ids):
            quote = quote_store.get_quote_by_id(int(quote_id))
            if quote:
                quotes.append(quote)
        return quotes

    @bp.route("/gallery", endpoint="gallery")
    def gallery():
        page = max(1, request.args.get("page", 1, type=int) or 1)
        subject_query = " ".join((request.args.get("subject") or "").split()).strip()
        images, page, total_pages, total_images = services.list_gallery_images(
            page=page,
            per_page=18,
            subject_query=subject_query,
        )
        for image in images:
            image["linked_quotes"] = _resolve_quotes(image.get("quote_ids", []))[:4]
        return render_template(
            "gallery.html",
            images=images,
            page=page,
            total_pages=total_pages,
            total_images=total_images,
            subject_query=subject_query,
            subject_directory=services.get_gallery_subject_directory(limit=120),
        )

    @bp.route("/gallery/add", methods=["GET", "POST"], endpoint="gallery_add_image")
    def gallery_add_image():
        form_error = ""
        form_defaults = {
            "submitter_name": "",
            "subjects": "",
            "image_context": "",
            "quote_id": "",
        }
        quote_options = _sorted_quotes_newest_first()[:500]

        if request.method == "POST":
            submitter_name = (request.form.get("submitter_name") or "").strip()
            subjects_raw = (request.form.get("subjects") or "").strip()
            image_context = (request.form.get("image_context") or "").strip()
            quote_id_raw = (request.form.get("quote_id") or "").strip()
            quote_ids = services.parse_int_id_list([quote_id_raw], limit=1)
            invalid_quote_ids = [
                quote_id
                for quote_id in quote_ids
                if not quote_store.get_quote_by_id(int(quote_id))
            ]
            form_defaults = {
                "submitter_name": submitter_name,
                "subjects": subjects_raw,
                "image_context": image_context,
                "quote_id": quote_id_raw,
            }

            if invalid_quote_ids:
                form_error = (
                    "Unknown quote IDs: "
                    + ", ".join(str(quote_id) for quote_id in invalid_quote_ids)
                )
            else:
                image_payload, image_error = services.create_gallery_image(
                    file_storage=request.files.get("image_file"),
                    submitter_name=submitter_name,
                    subjects=subjects_raw,
                    context=image_context,
                    quote_ids=quote_ids,
                )
                if image_error:
                    form_error = image_error
                elif image_payload:
                    return redirect(
                        url_for("gallery_image", image_id=int(image_payload["id"]))
                    )
                else:
                    form_error = "Unable to add image."

        return render_template(
            "gallery_add.html",
            form_error=form_error,
            form_defaults=form_defaults,
            quote_options=quote_options,
        )

    @bp.route("/gallery/<int:image_id>", methods=["GET", "POST"], endpoint="gallery_image")
    def gallery_image(image_id: int):
        image = services.get_gallery_image_by_id(image_id)
        if not image:
            abort(404)

        notice = ""
        form_error = ""
        quote_ids_input = ", ".join(str(quote_id) for quote_id in image["quote_ids"])

        if request.method == "POST":
            quote_ids_input = (request.form.get("quote_ids") or "").strip()
            quote_ids = services.parse_int_id_list(quote_ids_input, limit=40)
            invalid_quote_ids = [
                quote_id
                for quote_id in quote_ids
                if not quote_store.get_quote_by_id(int(quote_id))
            ]
            if invalid_quote_ids:
                form_error = (
                    "Unknown quote IDs: "
                    + ", ".join(str(quote_id) for quote_id in invalid_quote_ids)
                )
            elif not services.set_gallery_links_for_image(image_id, quote_ids):
                form_error = "Unable to update quote links for this image."
            else:
                image = services.get_gallery_image_by_id(image_id) or image
                quote_ids_input = ", ".join(
                    str(quote_id) for quote_id in image.get("quote_ids", [])
                )
                notice = "Quote links updated."

        linked_quotes = _resolve_quotes(image.get("quote_ids", []))
        caption_text = linked_quotes[0].quote if linked_quotes else image["submitter_name"]

        return render_template(
            "gallery_image.html",
            image=image,
            linked_quotes=linked_quotes,
            caption_text=caption_text,
            quote_ids_input=quote_ids_input,
            notice=notice,
            form_error=form_error,
        )
