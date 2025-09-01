from flask import Flask, jsonify

app = Flask(__name__)

@app.get("/health")
def health():
    return "OK", 200

@app.get("/api/ping")
def ping():
    return jsonify(ok=True, source="amoapp"), 200
