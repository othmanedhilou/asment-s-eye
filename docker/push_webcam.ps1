# Pousse la webcam locale vers mediamtx en RTSP/TCP.
# Le nom du peripherique change d'une machine a l'autre : le lister avec
#   ffmpeg -list_devices true -f dshow -i dummy
param(
  [string]$Device = "Integrated Camera",
  [string]$Url = "rtsp://localhost:8554/webcam"
)

ffmpeg -f dshow -i video="$Device" `
  -c:v libx264 -preset ultrafast -tune zerolatency `
  -rtsp_transport tcp -f rtsp $Url
