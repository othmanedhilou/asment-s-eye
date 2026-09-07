# Ciment's Eye — image de deploiement.
#
# POURQUOI UNE IMAGE PLUTOT QU'UNE INSTALLATION
#
# Le groupe s'apprete a equiper CHAQUE SITE d'une machine. Installer a la main
# Python, les dependances systeme d'OpenCV, le runtime OpenVINO et les modeles
# sur chacune, c'est autant d'occasions de se tromper — et surtout aucune
# garantie que deux sites executent la meme chose. Une image resout les deux :
# on la construit une fois, on la lance partout avec la meme commande.
#
# TROIS PIEGES SONT TRAITES ICI, ET AUCUN N'EST EVIDENT
#
#   torch en version CPU. La machine cible n'a pas de GPU. La roue par defaut
#   embarque les bibliotheques CUDA — plusieurs gigaoctets qui ne serviront
#   jamais. L'index CPU les ecarte.
#
#   easyocr et opencv. easyocr depend d'opencv-python-HEADLESS, qui REMPLACE
#   opencv-python et casse la capture video. On installe donc ses dependances
#   a la main, puis easyocr sans les siennes (--no-deps).
#
#   OMP_NUM_THREADS. OpenVINO alloue par defaut tous les coeurs a CHAQUE
#   modele. Avec plusieurs modeles en parallele sur deux coeurs, la machine
#   passe son temps a changer de contexte. La variable doit etre posee AVANT
#   tout import du runtime — d'ou sa declaration ici, dans l'environnement.

FROM python:3.13-slim

# Bibliotheques systeme exigees par OpenCV : sans elles, `import cv2` echoue
# a l'execution avec une erreur de bibliotheque partagee introuvable, et non
# a la construction — la panne se decouvre donc au pire moment.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Les dependances d'abord, le code ensuite : Docker met en cache chaque etape,
# et le code change bien plus souvent que les dependances. Inverser les deux
# ferait reinstaller torch a chaque correction d'une ligne.
COPY requirements.txt ./

RUN pip install --upgrade pip \
 && pip install torch==2.13.0 torchvision==0.28.0 \
      --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt \
 && pip install scipy scikit-image python-bidi pyclipper shapely ninja \
 && pip install --no-deps easyocr==1.7.2

# Le code, la configuration, les modeles et l'interface.
COPY app/ ./app/
COPY web/ ./web/
COPY config/ ./config/
COPY models/ ./models/
COPY scripts/ ./scripts/

# Ce qui doit survivre au remplacement d'un conteneur : la base d'alertes et
# de passages, les captures et les clips. Declares en volumes pour qu'une
# mise a jour de l'image n'efface pas l'historique du site.
VOLUME ["/app/data", "/app/clips"]

EXPOSE 8000

# L'interface par defaut. La detection se lance avec la meme image, en
# surchargeant la commande — voir docker-compose.yml.
CMD ["python", "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
