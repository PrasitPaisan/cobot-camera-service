# Deploy as a Windows Service (on the server at the camera/PLC's LAN)

Target machine: the Windows Server physically on the camera/PLC's LAN segment
(currently `192.168.10.33`). This runs the project directly on Windows --
no Docker, no Hyper-V, no VM. The GigE camera and PLC are already L2-adjacent
to this machine (same subnet), so there is no networking workaround needed:
this sidesteps every problem hit trying to containerize it.

---

## 0. Prerequisites (check before starting)

- [ ] Confirm this server's NIC is actually on the camera/PLC's subnet:
  ```powershell
  Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias, IPAddress
  ```
  Should show an address in `192.168.10.0/24`.
- [ ] Confirm the server can reach the camera and PLC:
  ```powershell
  Test-Connection -ComputerName 192.168.10.54 -Count 2   # camera
  Test-Connection -ComputerName 192.168.10.51 -Count 2   # PLC
  ```
- [ ] Python 3.14 installed (`python --version`). Install from
  python.org if missing -- check "Add python.exe to PATH" during install.
- [ ] Git installed (`git --version`).

None of this requires a reboot or touches other services running on this
server.

---

## 1. Get the code onto the server

```powershell
cd C:\
git clone https://github.com/PrasitPaisan/cobot-camera-service.git
cd cobot-camera-service
```

(Needs network access to github.com, and the repo is private -- either
`git config` a stored credential/PAT first, or copy the folder over some
other way, e.g. a zip export, if this server has no GitHub access.)

---

## 2. Create `.env` with real production values

`.env` is gitignored (has secrets/IPs) -- it did **not** come with the
clone. Copy it from the known-working machine (the workstation this was
developed and tested on), or recreate it from `.env.example` with real
values:

```powershell
copy \\<path-to-workstation-share>\cobot-camera-service\.env .env
```

Double check at minimum: `CAMERA_IP`, `CAMERA_NET_IP` (must match *this*
server's NIC, not the workstation's), `PLC_HOST`, `PLC_PORT`,
`CAM_RESULT_OK_DEVICE`/`CAM_RESULT_NG_DEVICE`.

`CAMERA_NET_IP` is the IP of *this server's* NIC on the camera's subnet --
it will likely be `192.168.10.33` here, not whatever it was on the dev
workstation (`192.168.10.191`). Get it from step 0's `Get-NetIPAddress`
output.

---

## 3. Set up the Python environment

```powershell
cd C:\cobot-camera-service
python -m venv env
.\env\Scripts\pip install -r requirements.txt
```

This pulls `ultralytics` (and `torch` as its dependency) -- expect this
step to take a while and use a few GB of disk.

---

## 4. Vendor the Windows MVS SDK runtime (if not already in the repo copy)

Check these exist (they should -- committed to the repo):

```powershell
dir vendor\mvimport
dir vendor\mvs_runtime\Win64_x64
```

If `vendor\mvs_runtime\Win64_x64` is missing or incomplete, copy it from
`C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64` on a machine
that has the Hikrobot MVS SDK installed (see `app/camera/sdk_path.py` for
exactly what's needed).

---

## 5. Smoke-test by running it directly first

**Before** wrapping this in a service, run it in a console and confirm it
actually works on this hardware:

```powershell
.\env\Scripts\python main.py
```

Watch the log output for:
- `Camera connected at <CAMERA_IP>, free-run grabbing started` -- camera OK
- `Poll: M6000=...` repeating without errors -- PLC OK
- No `RuntimeError: open device fail!` -- if you see this, the camera isn't
  reachable from here yet (check `CAMERA_IP`/`CAMERA_NET_IP` in `.env`
  match this server's actual NIC and subnet).

Let it run for a minute, confirm it behaves, then `Ctrl+C` to stop before
continuing.

---

## 6. Install NSSM (service wrapper)

NSSM runs `python main.py` as a proper Windows service (auto-start on boot,
auto-restart on crash, logs redirected to files).

```powershell
winget install -e --id NSSM.NSSM
```

(Or download manually from nssm.cc if winget isn't available here.)

---

## 7. Create the service

```powershell
$svc = "CobotCameraService"
$pythonExe = "C:\cobot-camera-service\env\Scripts\python.exe"
$scriptPath = "C:\cobot-camera-service\main.py"
$workDir = "C:\cobot-camera-service"
$logDir = "C:\cobot-camera-service\logs"

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

nssm install $svc $pythonExe $scriptPath
nssm set $svc AppDirectory $workDir
nssm set $svc AppStdout "$logDir\service.log"
nssm set $svc AppStderr "$logDir\service.log"
nssm set $svc AppRotateFiles 1
nssm set $svc AppRotateOnline 1
nssm set $svc AppRotateBytes 10485760   # rotate at 10MB
nssm set $svc Start SERVICE_AUTO_START
nssm set $svc AppExit Default Restart   # restart on crash
```

---

## 8. Start it and verify

```powershell
nssm start $svc
Get-Service $svc
```

Tail the log to confirm it's behaving the same as the manual run in step 5:

```powershell
Get-Content "$logDir\service.log" -Wait -Tail 50
```

Trigger a real PLC cycle if possible and confirm:
- trigger detected -> snapshot -> OK/NG written back to the PLC
- images landing in `adddata\OK\` / `adddata\NG\`

---

## 9. Day-2 operations

- **Restart:** `nssm restart CobotCameraService`
- **Stop:** `nssm stop CobotCameraService`
- **Uninstall:** `nssm remove CobotCameraService confirm`
- **Update code:**
  ```powershell
  nssm stop CobotCameraService
  cd C:\cobot-camera-service
  git pull
  .\env\Scripts\pip install -r requirements.txt   # if requirements changed
  nssm start CobotCameraService
  ```
- **Logs:** `C:\cobot-camera-service\logs\service.log` (auto-rotates at 10MB)
