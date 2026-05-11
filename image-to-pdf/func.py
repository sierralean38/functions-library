import io
import os
import json
import base64
import oci
from fdk import response
from pdf2image import convert_from_path

BUCKET_NAME = os.environ.get('BUCKET_NAME')

def handler(ctx, data: io.BytesIO=None):
    try:
        body = json.loads(data.getvalue())
        pdf_file = body.get("file_name")

        if not pdf_file:
            return response.Response(
                ctx,
                response_data=json.dumps({"error": "Falta parámetro file_name"}),
                headers={"Content-Type": "application/json"}
            )

        signer = oci.auth.signers.get_resource_principals_signer()
        object_storage = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
        namespace = os.environ.get('NAMESPACE_NAME')

        pdf_obj = object_storage.get_object(namespace, BUCKET_NAME, pdf_file)
        pdf_content = pdf_obj.data.content

        temp_pdf_path = "/tmp/input.pdf"
        with open(temp_pdf_path, "wb") as f:
            f.write(pdf_content)

        pages = convert_from_path(temp_pdf_path, dpi=100, fmt="png", output_folder="/tmp")

        uploaded_files = []
        images_base64 = []
        for i, page in enumerate(pages):
            img_path = f"/tmp/page_{i+1}.jpg"
            page.save(img_path, "JPEG", quality=60)

            object_name = f"{pdf_file}_page_{i+1}.jpg"

            with open(img_path, "rb") as img_file:
                object_storage.put_object(namespace, BUCKET_NAME, "ImagenGenerada/" + object_name, img_file)

            uploaded_files.append(object_name)

            with open(img_path, "rb") as img_file:
                encoded = base64.b64encode(img_file.read()).decode("utf-8")
                images_base64.append({"name": object_name, "base64": encoded})

        for f in os.listdir("/tmp"):
            try:
                os.remove(os.path.join("/tmp", f))
            except Exception as e:
                print(f"No se pudo borrar {f}: {e}")

        return response.Response(
            ctx,
            response_data=json.dumps({"uploaded_files": uploaded_files, "images_base64": images_base64}),
            headers={"Content-Type": "application/json"}
        )

    except Exception as e:
        return response.Response(
            ctx,
            response_data=json.dumps({"error": str(e)}),
            headers={"Content-Type": "application/json"}
        )
