"""Interface Streamlit pour tester les modèles SmokeWatch sur image/vidéo/webcam.

Lancer avec :
    streamlit run web/test_app.py
"""

import tempfile
from pathlib import Path

import cv2
import streamlit as st
from ultralytics import YOLO

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

st.set_page_config(page_title="SmokeWatch - Test modèles", page_icon="🔥", layout="wide")


@st.cache_resource
def load_model(model_path: str) -> YOLO:
    return YOLO(model_path)


def list_models() -> dict[str, Path]:
    return {p.stem.replace("smokewatch_", "").replace("_best", ""): p for p in sorted(MODELS_DIR.glob("*.pt"))}


def run_on_image(model: YOLO, image_path: str, conf: float):
    results = model.predict(source=image_path, conf=conf, save=False)
    r = results[0]
    annotated = r.plot()
    annotated = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
    return annotated, r


def run_on_video(model: YOLO, video_path: str, conf: float, frame_placeholder, stop_flag):
    cap = cv2.VideoCapture(video_path)
    detections_summary = {}

    while cap.isOpened():
        if stop_flag():
            break
        ok, frame = cap.read()
        if not ok:
            break

        results = model.predict(source=frame, conf=conf, verbose=False)
        r = results[0]
        annotated = r.plot()
        annotated = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        frame_placeholder.image(annotated, channels="RGB", use_container_width=True)

        if r.boxes is not None:
            for box in r.boxes:
                cls_name = model.names[int(box.cls[0])]
                detections_summary[cls_name] = detections_summary.get(cls_name, 0) + 1

    cap.release()
    return detections_summary


def main():
    st.title("🔥 SmokeWatch — Test des modèles")
    st.caption("Interface de validation rapide des modèles YOLO entraînés sur Kaggle.")

    models = list_models()
    if not models:
        st.error(f"Aucun modèle trouvé dans {MODELS_DIR}")
        return

    with st.sidebar:
        st.header("Configuration")
        model_key = st.selectbox("Modèle", options=list(models.keys()))
        conf = st.slider("Seuil de confiance", 0.05, 0.95, 0.25, 0.05)
        source_type = st.radio("Source", ["Image", "Vidéo", "Webcam"])

    model_path = models[model_key]
    model = load_model(str(model_path))

    st.subheader(f"Modèle sélectionné : `{model_path.name}`")
    st.write("Classes :", ", ".join(model.names.values()))

    if source_type == "Image":
        uploaded = st.file_uploader("Choisir une image", type=["jpg", "jpeg", "png", "bmp"])
        if uploaded:
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix) as tmp:
                tmp.write(uploaded.read())
                tmp_path = tmp.name

            annotated, r = run_on_image(model, tmp_path, conf)
            st.image(annotated, use_container_width=True)

            if r.boxes is not None and len(r.boxes) > 0:
                st.subheader("Détections")
                for box in r.boxes:
                    cls_name = model.names[int(box.cls[0])]
                    conf_val = float(box.conf[0])
                    st.write(f"- **{cls_name}** — confiance {conf_val:.2f}")
            else:
                st.info("Aucune détection.")

    elif source_type == "Vidéo":
        uploaded = st.file_uploader("Choisir une vidéo", type=["mp4", "avi", "mov", "mkv"])
        if uploaded:
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix) as tmp:
                tmp.write(uploaded.read())
                tmp_path = tmp.name

            frame_placeholder = st.empty()
            stop = st.button("Arrêter")
            summary = run_on_video(model, tmp_path, conf, frame_placeholder, lambda: stop)

            st.subheader("Résumé des détections")
            if summary:
                for cls_name, count in sorted(summary.items(), key=lambda x: -x[1]):
                    st.write(f"- **{cls_name}** : {count} frame(s)")
            else:
                st.info("Aucune détection sur la vidéo.")

    else:  # Webcam
        run = st.checkbox("Démarrer la webcam")
        frame_placeholder = st.empty()
        if run:
            cap = cv2.VideoCapture(0)
            while run and cap.isOpened():
                ok, frame = cap.read()
                if not ok:
                    st.error("Impossible de lire la webcam.")
                    break
                results = model.predict(source=frame, conf=conf, verbose=False)
                annotated = results[0].plot()
                annotated = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
                frame_placeholder.image(annotated, channels="RGB", use_container_width=True)
            cap.release()


if __name__ == "__main__":
    main()
