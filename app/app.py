from flask import Flask, render_template, request, redirect, url_for, flash
import subprocess
import os
import json
import docker
import logging
from datetime import datetime
import debugpy
 

# Create a logger
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)  # Set the logging level to DEBUG

# Create a FileHandler
file_handler = logging.FileHandler('app.log')
file_handler.setLevel(logging.DEBUG)  # Set the logging level to DEBUG for the file handler

# Create a StreamHandler for stdout
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG)  # Set the logging level to DEBUG for the stream handler

# Create a formatter and set the formatter for the handlers
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

# Add the handlers to the logger
logger.addHandler(file_handler)
logger.addHandler(stream_handler)


app = Flask(__name__)
app.secret_key = os.urandom(24)  # Change to a fixed value for consistent sessions
 
@app.route('/')
def index():
    error = None  # Initialize the error variable
    is_initialized = False
    container_volumes = {}  # Initialize container_volumes dictionary
    
    client = docker.from_env()  # Create a Docker client
    containers = client.containers.list(all=True)  # List all containers including stopped ones

    try:
        # Check if the repository is initialized by listing snapshots.
        # If the repository is not initialized, this command will raise an exception.
        subprocess.check_call(["restic", "snapshots"])
        is_initialized = True
        output = subprocess.check_output(["restic", "snapshots", "--json"])
        snapshots = json.loads(output.decode('utf-8'))
        logging.info(f"snapshots: {snapshots}")
    except subprocess.CalledProcessError:
        # CalledProcessError occurs when the command returns a non-zero exit code
        snapshots = []
    except Exception as e:
        snapshots = []
        error = str(e)

    # Populate the container_volumes dictionary
    for container in containers:
        container_volumes[container.name] = []
        for mount in container.attrs['Mounts']:
            if 'Source' in mount and 'Destination' in mount:
                volume_info = {'Source': mount['Source'], 'Destination': mount['Destination']}
                # Filter snapshots for the specific volume of the container
                volume_snapshots = [
                    snapshot for snapshot in snapshots
                    if 'tags' in snapshot 
                    and snapshot['tags'][0] == "container=" + container.name 
                    and snapshot['tags'][1] ==  "volume=" + mount['Destination']
                ]
                volume_info['snapshots'] = volume_snapshots

                if volume_snapshots:  # If there are any snapshots for this volume
                    # Convert snapshot times to datetime objects and find the latest one 
                    latest_snapshot = max(volume_snapshots, key=lambda x: datetime.strptime(x['time'][:-4] + 'Z', '%Y-%m-%dT%H:%M:%S.%fZ'))
                    volume_info['latest_snapshot'] = latest_snapshot

                container_volumes[container.name].append(volume_info)

    logging.info(f"container_volumes: {container_volumes}")
    return render_template(
        'index.html',
        snapshots=snapshots,
        error=error,
        is_initialized=is_initialized,
        container_volumes=container_volumes  # Pass container_volumes to the template
    )


@app.route('/initialize', methods=['POST'])
def initialize():
    try:
        subprocess.check_call(["restic", "init"])
        flash("Repository successfully initialized", "success")
    except subprocess.CalledProcessError as e:
        flash(f"Error initializing repository: {e.output}", "danger")
    except Exception as e:
        flash(f"Unexpected error: {str(e)}", "danger")
    return redirect(url_for('index'))

@app.route('/restore', methods=['POST'])
def restore():
    snapshot_id = request.form.get('snapshot_id')
    if snapshot_id:
        try:
            subprocess.check_call(["restic", "restore", snapshot_id, "--target", "/path-to-restore"])
            flash(f"Successfully restored snapshot {snapshot_id}", "success")
        except subprocess.CalledProcessError as e:
            flash(f"Error restoring snapshot {snapshot_id}: {e.output}", "danger")
        except Exception as e:
            flash(f"Unexpected error: {str(e)}", "danger")
    else:
        flash("No snapshot ID provided", "warning")
    return redirect(url_for('index'))


@app.route('/backup-volume', methods=['POST'])
def backup_volume():
    # Retrieve the form data
    volume = request.form.get('volume')  # Source of the volume on the host
    destination = request.form.get('destination')  # Path inside the container where the volume is mounted
    container_name = request.form.get('container_name')  # Name of the container
    
    # Define the path to the shared intermediate backup volume inside the restic-ui container
    intermediate_backup_path = '/intermediatebackup'
    tar_file_path = os.path.join(intermediate_backup_path, f"{container_name}{destination}", 'backup.tar')

    # Create a tag with the container name and volume name.
    tag = f"container={container_name},volume={destination}"

    
    logging.info(f"tag: {tag}")
    logging.info(f"volume: {volume}")
    logging.info(f"destination: {destination}")
    logging.info(f"container_name: {container_name}") 


    
    try:

        # ensure dirs exist
        backup_dir = f"/backup/{container_name}{destination}"

        cmd = [
            'docker', 'run', '--rm',
            '--volumes-from', container_name,
            '-v', 'restic-ui_intermediate-backup:/backup',
            'ubuntu', 'mkdir', '-p', backup_dir
        ]
        # Run the command to create the tar file
        logging.info(f"Running command: {' '.join(cmd)}")
        subprocess.check_call(cmd)

        # Construct the command to create a tar file of the volume in the intermediate backup volume
        cmd = [
            'docker', 'run', '--rm',
            '--volumes-from', container_name,
            '-v', 'restic-ui_intermediate-backup:/backup',
            '-w', backup_dir,
            'ubuntu', 'tar', 'cvf', 'backup.tar', destination
        ]
        # Run the command to create the tar file
        logging.info(f"Running command: {' '.join(cmd)}")
        subprocess.check_call(cmd)
        

        # Define the Restic backup command
        backup_cmd = [
            'restic', 'backup', tar_file_path, 
            '--tag', tag,  # Add the tag to the snapshot
            '--host', container_name
            ]
        
        logging.info(f"Running Restic backup command: {' '.join(backup_cmd)}")
        subprocess.check_call(backup_cmd)
        
        # After the successful backup, remove the tar file from the intermediate backup volume
        os.remove(tar_file_path)
        
        flash("Volume successfully backed up with Restic", "success")
    except subprocess.CalledProcessError as e:
        flash(f"Error during the backup process: {e.output}", "danger")
        logging.error(f"Command returned non-zero exit code: {e.returncode}")
        logging.error(f"Standard Output: {e.stdout}")
        logging.error(f"Standard Error: {e.stderr}")
    except Exception as e:
        flash(f"Unexpected error during backup: {str(e)}", "danger")
        logging.error(f"Unexpected error during backup: {str(e)}")
    return redirect(url_for('index'))

@app.route('/restore-volume', methods=['POST'])
def restore_volume():
 
    # Retrieve the form data
    volume = request.form.get('volume')  # Source of the volume on the host
    container_name = request.form.get('container_name')  # Name of the container
    logging.info(f"volume: {volume}") 
    logging.info(f"container_name: {container_name}") 

    try:
        # Implement the logic to restore volume here
        # Use the tags to find the appropriate snapshot and restore it
        flash("Volume successfully restored", "success")
        
    except Exception as e:
        logging.error(f"Unexpected error during restore: {str(e)}", exc_info=True)
        flash(f"Unexpected error: {str(e)}", "danger")
        
    return redirect(url_for('index'))


if __name__ == '__main__':

    app.run(host='0.0.0.0', port=5000, debug=True)






 