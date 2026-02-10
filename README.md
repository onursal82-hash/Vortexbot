# Vortex AI Trading Bot (Distribution Package)

## 🚀 Deployment Instructions (VPS / Cloud)

This package is Docker-ready for 24/7 cloud hosting.

### 1. Prerequisites
- A VPS (Virtual Private Server) with Docker installed.
- (Optional) Domain name pointing to the VPS IP.

### 2. Quick Start (Docker)
Run these commands in the project directory:

```bash
# Build the container
docker build -t vortex-bot .

# Run the container (Detached, Port 5300, Auto-Restart)
docker run -d -p 5300:5300 --name vortex --restart unless-stopped vortex-bot
```

### 3. Access
- **Web**: `http://YOUR_VPS_IP:5300`
- **Mobile App**: Open the URL on your phone (Chrome/Safari), tap **"Add to Home Screen"**. The Vortex Logo will appear as the app icon.

## 📱 PWA / Mobile App Features
- **App Icon**: Vortex Ultra-Final Logo
- **Display**: Fullscreen (Standalone)
- **Theme**: Dark Teal (#0d2b2d)
- **Persistence**: Runs in the background on the server.

## ⚠️ Maintenance
- **Logs**: `docker logs -f vortex`
- **Stop**: `docker stop vortex`
- **Restart**: `docker restart vortex`
