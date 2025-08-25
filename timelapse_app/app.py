#!/usr/bin/env python3
"""
Raspberry Pi Timelapse Web Controller
Main Flask application for controlling timelapse photography
"""

import os
import json
import threading
import time
import zipfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import psutil
import cv2

from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, Response
import schedule

# Try to import camera modules (will work on Pi, fallback for development)
try:
    from picamera2 import Picamera2
    CAMERA_AVAILABLE = True
except ImportError:
    print("Warning: picamera2 not available. Running in development mode.")
    CAMERA_AVAILABLE = False

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this'

# Configuration
CURRENT_PATH = os.getcwd()
UPLOAD_FOLDER = CURRENT_PATH + '/timelapse_images'
PROJECTS_FOLDER = CURRENT_PATH + '/timelapse_projects'
USB_MOUNT_PATH = '/media'

# Check if folders exist, and, if not, create
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROJECTS_FOLDER, exist_ok=True)

# Global variables
current_session = None
camera = None
capture_thread = None
is_capturing = False

class TimelapseSession:
    def __init__(self, name, interval_seconds, duration_hours, resolution, quality):
        self.name = name
        self.interval_seconds = interval_seconds
        self.duration_hours = duration_hours
        self.resolution = resolution
        self.quality = quality
        self.start_time = None
        self.end_time = None
        self.images_captured = 0
        self.status = 'created'  # created, running, paused, completed, stopped
        self.folder_path = os.path.join(PROJECTS_FOLDER, name)
        self.last_image_path = None
        
    def to_dict(self):
        total_images = int((self.duration_hours * 3600) / self.interval_seconds)
        progress = (self.images_captured / total_images * 100) if total_images > 0 else 0
        
        return {
            'name': self.name,
            'interval_seconds': self.interval_seconds,
            'duration_hours': self.duration_hours,
            'resolution': self.resolution,
            'quality': self.quality,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'images_captured': self.images_captured,
            'status': self.status,
            'progress': progress,
            'total_images': total_images,
            'last_image_path': self.last_image_path,
            'folder_size': self.get_folder_size()
        }
    
    def get_folder_size(self):
        if os.path.exists(self.folder_path):
            total_size = sum(os.path.getsize(os.path.join(self.folder_path, f)) 
                           for f in os.listdir(self.folder_path) 
                           if os.path.isfile(os.path.join(self.folder_path, f)))
            return self.format_bytes(total_size)
        return "0 B"
    
    @staticmethod
    def format_bytes(bytes_size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_size < 1024.0:
                return f"{bytes_size:.1f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.1f} TB"

def init_camera():
    global camera
    if CAMERA_AVAILABLE and camera is None:
        try:
            camera = Picamera2()
            return True
        except Exception as e:
            print(f"Failed to initialize camera: {e}")
            return False
    return CAMERA_AVAILABLE

def capture_image(session, image_number):
    """Capture a single image"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{session.name}_{timestamp}_{image_number:06d}.jpg"
    filepath = os.path.join(session.folder_path, filename)
    
    if CAMERA_AVAILABLE and camera:
        try:
            # Configure camera
            config = camera.create_still_configuration(
                main={"size": session.resolution}
            )
            camera.configure(config)
            camera.start()
            time.sleep(1)  # Camera warm-up
            camera.capture_file(filepath)
            camera.stop()
            session.last_image_path = filepath
            return True
        except Exception as e:
            print(f"Failed to capture image: {e}")
            return False
    else:
        # Create dummy image for development/testing
        with open(filepath, 'w') as f:
            f.write(f"Dummy image {image_number} at {timestamp}")
        session.last_image_path = filepath
        return True

def timelapse_worker(session):
    """Background worker for timelapse capture"""
    global is_capturing
    
    session.start_time = datetime.now()
    session.status = 'running'
    session.end_time = session.start_time + timedelta(hours=session.duration_hours)
    
    os.makedirs(session.folder_path, exist_ok=True)
    
    image_count = 0
    next_capture = time.time()
    
    while is_capturing and datetime.now() < session.end_time:
        if time.time() >= next_capture:
            if capture_image(session, image_count + 1):
                image_count += 1
                session.images_captured = image_count
                print(f"Captured image {image_count}")
            
            next_capture = time.time() + session.interval_seconds
        
        time.sleep(0.1)  # Small sleep to prevent CPU spinning
    
    session.status = 'completed' if datetime.now() >= session.end_time else 'stopped'
    is_capturing = False
    print(f"Timelapse completed. Total images: {image_count}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/system_info')
def system_info():
    """Get system information"""
    try:
        # Disk usage
        disk_usage = psutil.disk_usage('/')
        disk_free_gb = disk_usage.free / (1024**3)
        disk_total_gb = disk_usage.total / (1024**3)
        disk_used_percent = (disk_usage.used / disk_usage.total) * 100
        
        # USB devices
        usb_devices = []
        if os.path.exists(USB_MOUNT_PATH):
            usb_devices = [d for d in os.listdir(USB_MOUNT_PATH) 
                          if os.path.isdir(os.path.join(USB_MOUNT_PATH, d))]
        
        return jsonify({
            'camera_available': CAMERA_AVAILABLE,
            'disk_free_gb': round(disk_free_gb, 2),
            'disk_total_gb': round(disk_total_gb, 2),
            'disk_used_percent': round(disk_used_percent, 1),
            'usb_devices': usb_devices,
            'current_time': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/start_timelapse', methods=['POST'])
def start_timelapse():
    """Start a new timelapse session"""
    global current_session, capture_thread, is_capturing
    
    if is_capturing:
        return jsonify({'error': 'Timelapse already running'}), 400
    
    data = request.json
    
    # Validate inputs
    try:
        name = data['name'].replace(' ', '_')
        
        # Convert interval to seconds based on unit
        interval_value = float(data['interval_value'])
        interval_unit = data['interval_unit']
        if interval_unit == 'minutes':
            interval_seconds = interval_value * 60
        elif interval_unit == 'hours':
            interval_seconds = interval_value * 3600
        else:  # seconds
            interval_seconds = interval_value
        
        # Convert duration to hours based on unit
        duration_value = float(data['duration_value'])
        duration_unit = data['duration_unit']
        if duration_unit == 'minutes':
            duration_hours = duration_value / 60
        elif duration_unit == 'days':
            duration_hours = duration_value * 24
        else:  # hours
            duration_hours = duration_value
        
        resolution = tuple(map(int, data['resolution'].split('x')))
        quality = int(data['quality'])
        
        if interval_seconds < 1:
            raise ValueError("Interval must be at least 1 second")
        if duration_hours <= 0:
            raise ValueError("Duration must be positive")
            
    except (KeyError, ValueError) as e:
        return jsonify({'error': f'Invalid parameters: {str(e)}'}), 400
    
    # Check if camera is available
    if not init_camera():
        return jsonify({'error': 'Camera not available'}), 500
    
    # Create session
    current_session = TimelapseSession(name, int(interval_seconds), duration_hours, resolution, quality)
    
    # Start capture thread
    is_capturing = True
    capture_thread = threading.Thread(target=timelapse_worker, args=(current_session,))
    capture_thread.daemon = True
    capture_thread.start()
    
    return jsonify({'message': 'Timelapse started', 'session': current_session.to_dict()})

@app.route('/api/stop_timelapse', methods=['POST'])
def stop_timelapse():
    """Stop the current timelapse session"""
    global is_capturing, current_session
    
    if not is_capturing:
        return jsonify({'error': 'No timelapse running'}), 400
    
    is_capturing = False
    if current_session:
        current_session.status = 'stopped'
    
    return jsonify({'message': 'Timelapse stopped'})

@app.route('/api/status')
def get_status():
    """Get current timelapse status"""
    if current_session:
        return jsonify(current_session.to_dict())
    else:
        return jsonify({'status': 'idle'})

@app.route('/api/projects')
def list_projects():
    """List all timelapse projects"""
    projects = []
    if os.path.exists(PROJECTS_FOLDER):
        for project_name in os.listdir(PROJECTS_FOLDER):
            project_path = os.path.join(PROJECTS_FOLDER, project_name)
            if os.path.isdir(project_path):
                file_count = len([f for f in os.listdir(project_path) 
                                if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
                projects.append({
                    'name': project_name,
                    'file_count': file_count,
                    'folder_path': project_path
                })
    
    return jsonify(projects)

@app.route('/api/download/<project_name>')
def download_project(project_name):
    """Download project as ZIP file"""
    project_path = os.path.join(PROJECTS_FOLDER, project_name)
    
    if not os.path.exists(project_path):
        return jsonify({'error': 'Project not found'}), 404
    
    # Create ZIP file
    zip_path = f"/tmp/{project_name}.zip"
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for root, dirs, files in os.walk(project_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, project_path)
                zipf.write(file_path, arcname)
    
    return send_file(zip_path, as_attachment=True, download_name=f"{project_name}.zip")

@app.route('/api/transfer_usb/<project_name>', methods=['POST'])
def transfer_to_usb(project_name):
    """Transfer project to USB device"""
    data = request.json
    usb_device = data.get('usb_device')
    
    if not usb_device:
        return jsonify({'error': 'No USB device specified'}), 400
    
    project_path = os.path.join(PROJECTS_FOLDER, project_name)
    usb_path = os.path.join(USB_MOUNT_PATH, usb_device)
    
    if not os.path.exists(project_path):
        return jsonify({'error': 'Project not found'}), 404
    
    if not os.path.exists(usb_path):
        return jsonify({'error': 'USB device not found'}), 404
    
    try:
        destination = os.path.join(usb_path, project_name)
        shutil.copytree(project_path, destination, dirs_exist_ok=True)
        return jsonify({'message': f'Project transferred to USB: {usb_device}'})
    except Exception as e:
        return jsonify({'error': f'Transfer failed: {str(e)}'}), 500

@app.route('/api/delete_project/<project_name>', methods=['DELETE'])
def delete_project(project_name):
    """Delete a project"""
    project_path = os.path.join(PROJECTS_FOLDER, project_name)
    
    if not os.path.exists(project_path):
        return jsonify({'error': 'Project not found'}), 404
    
    try:
        shutil.rmtree(project_path)
        return jsonify({'message': 'Project deleted'})
    except Exception as e:
        return jsonify({'error': f'Delete failed: {str(e)}'}), 500

def generate_frames():
    if not init_camera():
        return
    camera.start()
    while True:
        try:
            frame = camera.capture_array()
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        except Exception as e:
            print(f"Stream error: {e}")
            break
    camera.stop()

@app.route('/api/preview')
def preview():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    # Create necessary directories
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(PROJECTS_FOLDER, exist_ok=True)
    
    # Run the Flask app
    app.run(host='0.0.0.0', port=5000, debug=False)
