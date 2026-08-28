#!/bin/sh
while true; do
  ffmpeg -f dshow -i video="Integrated Camera" \
    -c:v libx264 -preset ultrafast -tune zerolatency \
    -rtsp_transport tcp -f rtsp \
    rtsp://localhost:8554/webcam
  echo "ffmpeg s'est arrêté, redémarrage dans 2s..."
  sleep 2
done
