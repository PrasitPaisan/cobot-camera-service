# Linux-only -- the vendored Hikrobot MVS SDK needs its Linux runtime placed
# under vendor/mvs_runtime/linux64/ (see app/camera/sdk_path.py) before this
# image can actually open the camera. Must run with network_mode: host (see
# docker-compose.yml) -- the GigE camera and PLC both need L2-adjacent /
# directly-routable access to the host's physical NIC, which Docker's default
# bridge/NAT networking cannot provide.
FROM python:3.14-slim

WORKDIR /app

# libusb/libglib/libgomp cover the common native deps of the Hikrobot SDK and
# opencv-python-headless/numpy -- add more here if `python main.py` complains
# about a missing .so once the real SDK is in place.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libusb-1.0-0 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY main.py .
# Only the Linux half of vendor/ -- the Windows wrapper + DLLs
# (vendor/mvimport, vendor/mvs_runtime/Win64_x64) are dead weight in a Linux
# image (~120MB of DLLs that will never run here).
COPY vendor/mvimport_linux ./vendor/mvimport_linux
COPY vendor/mvs_runtime/linux64 ./vendor/mvs_runtime/linux64
COPY best_v3.pt .

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
