import os
import csv
import time
import pandas as pd
from flask import Flask, request, jsonify, render_template, url_for, Response
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Say
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# Load environment variables
load_dotenv(dotenv_path='.env')

app = Flask(__name__)

# Twilio configuration
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

# For local testing, use localhost; in production, use domain
BASE_URL = os.getenv('BASE_URL', 'http://localhost:5000')


# Google Sheets configuration
GOOGLE_CREDENTIALS_FILE = os.getenv(
    'GOOGLE_CREDENTIALS_FILE', 'credentials.json')
SCOPES = ['https://www.googleapis.com/auth/spreadsheets',
          'https://www.googleapis.com/auth/drive']

# Initialize Twilio client
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Store call data
calls_data = {}


def load_numbers_from_csv(file_path):
    numbers = []
    required_field = 'phone_number'

    try:
        with open(file_path, 'r') as file:
            reader = csv.DictReader(file)
            headers = reader.fieldnames

            # Check if 'phone_number' column exists
            if required_field not in headers:
                app.logger.error(f"Missing required column: '{required_field}'")
                return []

            for row in reader:
                phone = row.get(required_field, '').strip()
                if phone and phone.isdigit():  # Basic validation
                    numbers.append(row)
                else:
                    app.logger.warning(f"Invalid or missing phone number in row: {row}")
    except Exception as e:
        app.logger.error(f"Error reading CSV: {str(e)}")
    return numbers




def load_numbers_from_google_sheet(sheet_id, worksheet_name='Sheet1'):
    try:
        # Setup the Google Sheets API
        credentials = ServiceAccountCredentials.from_json_keyfile_name(
            GOOGLE_CREDENTIALS_FILE, SCOPES)
        gc = gspread.authorize(credentials)

        # Open the spreadsheet and worksheet
        sheet = gc.open_by_key(sheet_id)
        worksheet = sheet.worksheet(worksheet_name)

        # Get all values and convert to list of dictionaries
        records = worksheet.get_all_records()
        return records
    except Exception as e:
        app.logger.error(f"Error loading Google Sheet: {str(e)}")
        return []


def make_call(phone_data):
    with app.app_context(): 
        try:
            phone_number = phone_data.get('phone_number')
            print("Loaded phone data:", phone_data)
            print("Number of entries:", len(phone_data))
            if not phone_number:
                return None

            # Additional data that might be used in the message
            name = phone_data.get('name', '')

            # Build URLs manually
            call_url = f"{BASE_URL}/handle-call?name={name}"
            status_cb_url = f"{BASE_URL}/call-status"

            # Make the call
            call = client.calls.create(
            to=phone_number,
            from_=TWILIO_PHONE_NUMBER,
            url=call_url,
            record=True,
            status_callback=status_cb_url,
            status_callback_event=['completed']
            )

            # Store call info
            calls_data[call.sid] = {
                'phone_number': phone_number,
                'name': name,
                'status': 'initiated',
                'transcript': None,
                'recording_url': None
            }

            return call.sid
        except Exception as e:
            app.logger.error(f"Error making call to {phone_number}: {str(e)}")
            return None


def process_calls_in_batches(phone_data_list, batch_size=100):
    total_calls = len(phone_data_list)
    call_sids = []

    for i in range(0, total_calls, batch_size):
        batch = phone_data_list[i:i+batch_size]
        with ThreadPoolExecutor(max_workers=10) as executor:
            batch_sids = list(executor.map(make_call, batch))
            call_sids.extend([sid for sid in batch_sids if sid])

        # Wait a bit between batches to avoid hitting rate limits
        if i + batch_size < total_calls:
            time.sleep(2)

    return call_sids


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/handle-call', methods=['POST'])
def handle_call():
    response = VoiceResponse()

    # Get the name parameter if available
    name = request.args.get('name', '')
    greeting = "Hello" if not name else f"Hello {name}"

    # Customize your message here
    response.say(f"{greeting}, this is an automated call from dowell. "
                 "This call is being recorded for quality and training purposes.",
                 voice='alice')

    response.pause(length=1)

    # Add your custom message content here
    response.say("Thank you for your time. Have a great day!", voice='alice')

    # Record the call
    response.record(action=url_for('recording_callback'),
                    transcribe=True,
                    transcribeCallback=url_for('transcription_callback'))

    return Response(str(response), mimetype='text/xml')


@app.route('/call-status', methods=['POST'])
def call_status_callback():
    """Handle call status updates"""
    call_sid = request.form.get('CallSid')
    status = request.form.get('CallStatus')

    if call_sid in calls_data:
        calls_data[call_sid]['status'] = status

    return '', 204


@app.route('/recording-callback', methods=['POST'])
def recording_callback():
    call_sid = request.form.get('CallSid')
    recording_url = request.form.get('RecordingUrl')

    if call_sid in calls_data:
        calls_data[call_sid]['recording_url'] = recording_url

    return '', 204


@app.route('/transcription-callback', methods=['POST'])
def transcription_callback():
    call_sid = request.form.get('CallSid')
    transcription_text = request.form.get('TranscriptionText')

    if call_sid in calls_data:
        calls_data[call_sid]['transcript'] = transcription_text

    return '', 204


@app.route('/make-calls', methods=['POST'])
def initiate_calls():
    data_source = request.form.get('data_source', 'csv')

    if data_source == 'csv':
        # Handle file upload
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Save the file temporarily
        temp_path = os.path.join('/tmp', file.filename)
        file.save(temp_path)

        # Load numbers from CSV
        phone_data = load_numbers_from_csv(temp_path)

        if not phone_data:
            return jsonify({'error': 'No valid phone numbers found in CSV. Please ensure it includes a "phone_number" column with numeric values.'}), 400

        # Remove the temporary file
        os.remove(temp_path)

    elif data_source == 'google_sheet':
        sheet_id = request.form.get('sheet_id')
        worksheet_name = request.form.get('worksheet_name', 'Sheet1')

        if not sheet_id:
            return jsonify({'error': 'Google Sheet ID is required'}), 400

        # Load numbers from Google Sheet
        phone_data = load_numbers_from_google_sheet(sheet_id, worksheet_name)

    else:
        return jsonify({'error': 'Invalid data source'}), 400

    if not phone_data:
        return jsonify({'error': 'No phone numbers found'}), 400

    # Process calls in batches to handle large volumes
    batch_size = int(request.form.get('batch_size', 100))
    call_sids = process_calls_in_batches(phone_data, batch_size)

    return jsonify({
        'message': f'Initiated {len(call_sids)} calls',
        'call_sids': call_sids
    })


@app.route('/calls-status', methods=['GET'])
def get_calls_status():
    """Get the status of all calls"""
    return jsonify(calls_data)


@app.route('/export-results', methods=['GET'])
def export_results():
    """Export call results as CSV"""
    format_type = request.args.get('format', 'csv')

    if format_type == 'csv':
        # Convert calls_data to DataFrame
        df = pd.DataFrame.from_dict(calls_data, orient='index')
        df['call_sid'] = df.index

        # Create CSV response
        csv_data = df.to_csv(index=False)
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-disposition": "attachment; filename=call_results.csv"}
        )
    else:
        return jsonify(calls_data)


if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)

    # Create a simple HTML template for the UI with improved status display and auto-refresh
    with open('templates/index.html', 'w') as f:
        f.write('''
<!DOCTYPE html>
<html>
<head>
    <title>DoWell Caller</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; }
        input, select { width: 100%; padding: 8px; box-sizing: border-box; }
        button { padding: 10px 15px; background-color: #4CAF50; color: white; border: none; cursor: pointer; }
        button:disabled { background-color: #999; cursor: not-allowed; }
        .results { margin-top: 20px; }
        table { border-collapse: collapse; width: 100%; margin-top: 10px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
    </style>
</head>
<body>
    <div class="container">
        <h1>DoWell Caller</h1>
        
        <form id="callForm">
            <div class="form-group">
                <label for="dataSource">Data Source:</label>
                <select id="dataSource" name="data_source">
                    <option value="csv">CSV File</option>
                    <option value="google_sheet">Google Sheet</option>
                </select>
            </div>
            
            <div id="csvSection" class="form-group">
                <label for="csvFile">CSV File:</label>
                <input type="file" id="csvFile" name="file">
                <small>CSV should have at least a 'phone_number' column</small>
            </div>
            
            <div id="googleSheetSection" class="form-group" style="display: none;">
                <div class="form-group">
                    <label for="sheetId">Google Sheet ID:</label>
                    <input type="text" id="sheetId" name="sheet_id">
                </div>
                <div class="form-group">
                                    <label for="worksheetName">Worksheet Name:</label>
                <input type="text" id="worksheetName" name="worksheet_name" value="Sheet1">
            </div>
        </div>
        
        <div class="form-group">
            <label for="batchSize">Batch Size:</label>
            <input type="number" id="batchSize" name="batch_size" value="100" min="1" max="500">
            <small>Number of calls to process in each batch</small>
        </div>
        
        <button type="submit">Start Calling</button>
    </form>
    
    <div class="results" id="callResults"></div>
    
    <button id="exportBtn" style="margin-top: 20px;">Export Results as CSV</button>
</div>

<script>
    const dataSourceSelect = document.getElementById('dataSource');
    const csvSection = document.getElementById('csvSection');
    const googleSheetSection = document.getElementById('googleSheetSection');
    const callForm = document.getElementById('callForm');
    const callResultsDiv = document.getElementById('callResults');
    const exportBtn = document.getElementById('exportBtn');
    
    dataSourceSelect.addEventListener('change', () => {
        if (dataSourceSelect.value === 'csv') {
            csvSection.style.display = 'block';
            googleSheetSection.style.display = 'none';
        } else {
            csvSection.style.display = 'none';
            googleSheetSection.style.display = 'block';
        }
    });
    
    callForm.addEventListener('submit', (e) => {
        e.preventDefault();
        startCalling();
    });
    
    function getStatusLabel(status) {
        const statusMap = {
            queued: { label: "Queued", color: "gray" },
            ringing: { label: "Ringing", color: "blue" },
            "in-progress": { label: "In Progress", color: "orange" },
            completed: { label: "Completed", color: "green" },
            busy: { label: "Busy", color: "red" },
            failed: { label: "Failed", color: "red" },
            "no-answer": { label: "No Answer", color: "red" },
            canceled: { label: "Canceled", color: "red" }
        };
        return statusMap[status] || { label: status, color: "black" };
    }
    
    function refreshCallStatus() {
        fetch('/calls-status')
        .then(response => response.json())
        .then(data => {
            if (Object.keys(data).length === 0) {
                // No calls yet, clear table
                callResultsDiv.innerHTML = '';
                return;
            }

            let html = '<table><tr><th>Call SID</th><th>Phone Number</th><th>Name</th><th>Status</th><th>Recording</th><th>Transcript</th></tr>';
            
            for (const [callSid, callData] of Object.entries(data)) {
                const statusInfo = getStatusLabel(callData.status);
                html += `<tr>
                    <td>${callSid}</td>
                    <td>${callData.phone_number}</td>
                    <td>${callData.name || '-'}</td>
                    <td style="color: ${statusInfo.color}; font-weight: bold;">${statusInfo.label}</td>
                    <td>${callData.recording_url ? '<a href="' + callData.recording_url + '" target="_blank">Listen</a>' : '-'}</td>
                    <td>${callData.transcript || '-'}</td>
                </tr>`;
            }
            
            html += '</table>';
            callResultsDiv.innerHTML = html;
        });
    }
    
    function startCalling() {
        const formData = new FormData(callForm);
        
        // Basic validation
        if (formData.get('data_source') === 'csv') {
            if (!formData.get('file').name) {
                alert('Please select a CSV file.');
                return;
            }
        } else {
            if (!formData.get('sheet_id')) {
                alert('Please enter a Google Sheet ID.');
                return;
            }
        }
        
        // Disable form during call initiation
        callForm.querySelector('button[type="submit"]').disabled = true;
        callResultsDiv.innerHTML = 'Starting calls...';
        
        fetch('/make-calls', {
            method: 'POST',
            body: formData
        })
        .then(res => res.json())
        .then(response => {
            if (response.error) {
                callResultsDiv.innerHTML = `<p style="color:red;">Error: ${response.error}</p>`;
            } else {
                callResultsDiv.innerHTML = `<p>${response.message}</p>`;
                refreshCallStatus();
            }
        })
        .catch(err => {
            callResultsDiv.innerHTML = `<p style="color:red;">Error: ${err.message}</p>`;
        })
        .finally(() => {
            callForm.querySelector('button[type="submit"]').disabled = false;
        });
    }
    
    exportBtn.addEventListener('click', () => {
        window.location.href = '/export-results?format=csv';
    });
    
    // Auto-refresh call statuses every 10 seconds
    setInterval(refreshCallStatus, 10000);
    
    // Initial load of call statuses
    refreshCallStatus();
</script>
</body>
</html>''')

app.run(debug=True)
