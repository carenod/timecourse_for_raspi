# Timecourse for Raspi
A Raspberry Pi timelapse system with a web-based interface.

After cloning the repo, create this file si the server runs on start up.

```ruby
sudo nano /etc/systemd/system/timelapse.service
```

And add the following text adapting the user and path adequatly: 

```ruby
[Unit]
Description=Raspberry Pi Timelapse Web Controller
After=network.target
Wants=network.target

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=/home/pi/timelapse_app
Environment=PATH=/home/pi/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 /home/pi/timelapse_app/app.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Then, enable and start the service:

```ruby
# Reload systemd to recognize the new service
sudo systemctl daemon-reload

# Enable the service to start at boot
sudo systemctl enable timelapse.service

# Start the service now
sudo systemctl start timelapse.service

# Check if it's running
sudo systemctl status timelapse.service
```

Here are some useful commands for managging the service:

```ruby
# Stop the service
sudo systemctl stop timelapse.service

# Restart the service
sudo systemctl restart timelapse.service

# View logs
sudo journalctl -u timelapse.service -f

# View recent logs
sudo journalctl -u timelapse.service --since "1 hour ago"

# Disable auto-start
sudo systemctl disable timelapse.service
```

In order to avoid the need of wifi connection, it is possible to create a self-contained local network using NetworkManager.

For this, first create a connection profile

```ruby
sudo nmcli connection add type wifi ifname wlan0 con-name timelapse_ap autoconnect yes ssid Pi_Timelapse_AP
```
You can replace `Pi_Timelapse_AP` with your Wi-Fi device name if different.

Then, set it to access point mode:

```ruby
sudo nmcli connection modify timelapse_ap 802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared
```
Add a password:

```ruby
sudo nmcli connection modify timelapse_ap wifi-sec.key-mgmt wpa-psk wifi-sec.psk "raspberry123"
```

And bring it up:

```ruby
sudo nmcli connection up timelapse_ap
```
