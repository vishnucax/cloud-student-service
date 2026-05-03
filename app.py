import os, json
import boto3
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, jsonify
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.ext.flask.middleware import XRayMiddleware

app = Flask(__name__)

# X-Ray tracing
xray_recorder.configure(service="student-service")
XRayMiddleware(app, xray_recorder)

# Config injected by Kubernetes ConfigMap
REGION = os.environ.get("AWS_REGION", "ap-south-2")
COURSE_URL = os.environ.get("COURSE_SERVICE_URL", "http://course-service")

# DynamoDB — credentials come from IRSA, no keys in code
dynamodb = boto3.resource("dynamodb", region_name=REGION)
students_table = dynamodb.Table("nived-student")

# Reusable HTTP session with retry (keeps TCP connections warm)
session = requests.Session()
retry = Retry(
    total=3,
    backoff_factor=0.3,
    status_forcelist=[502, 503, 504]
)
session.mount("http://", HTTPAdapter(max_retries=retry))


@app.route("/health")
def health():
    # Kubernetes readiness + liveness probe endpoint
    return jsonify({"status": "ok", "service": "student-service"}), 200


@app.route("/students/<student_id>", methods=["GET"])
def get_student(student_id):
    resp = students_table.get_item(Key={"id": student_id})
    item = resp.get("Item")

    if not item:
        return jsonify({"error": "Student not found"}), 404

    # Enrich with course data — graceful degradation if course-service is down
    course_code = item.get("course")

    if course_code:
        try:
            r = session.get(f"{COURSE_URL}/courses/{course_code}", timeout=2)
            item["course"] = (
                r.json() if r.ok else {"code": course_code, "title": None}
            )
        except requests.RequestException:
            item["course"] = {"code": course_code, "title": None}

    return jsonify(item), 200


@app.route("/students", methods=["GET"])
def list_students():
    resp = students_table.scan(Limit=50)
    return jsonify(resp.get("Items", [])), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, debug=False)