import os
import csv
import time
import pandas as pd
from flask import Flask, request, jsonify, render_template, url_for, Response
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Say, Gather
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from threading import Lock
import json
from io import StringIO

# Load environment variables
load_dotenv(dotenv_path='.env')

app = Flask(__name__)

# Twilio configuration
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

# For local testing, use localhost; in production, use domain
BASE_URL = os.getenv('BASE_URL', 'https://dowell-caller.onrender.com/')

google_credentials_json = os.getenv("GOOGLE_CREDENTIALS_FILE")

if not google_credentials_json:
    raise Exception("Missing GOOGLE_CREDENTIALS_FILE environment variable")

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
    try:
        with open(file_path, 'r') as file:
            reader = csv.DictReader(file)  # Use DictReader for easier key access
            for row in reader:
                phone = row.get('phone_number') or row.get('phone') or row.get('Phone')  # fallback keys
                name = row.get('name', '')
                message = row.get('message', '')
                if phone and phone.strip().isdigit():
                    numbers.append({'phone_number': phone.strip(), 'name': name.strip(), 'message': message.strip()})
    except Exception as e:
        app.logger.error(f"Error reading CSV: {str(e)}")
    return numbers

def load_numbers_from_google_sheet(sheet_id, worksheet_name='Sheet1'):
    try:
        # credentials = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDENTIALS_FILE, SCOPES)
        credentials_dict = json.loads(google_credentials_json)
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, SCOPES)
        gc = gspread.authorize(credentials)
        sheet = gc.open_by_key(sheet_id)
        worksheet = sheet.worksheet(worksheet_name)
        records = worksheet.get_all_records()
        # Ensure keys exist
        for record in records:
            record.setdefault('name', '')
            record.setdefault('message', '')
        return records
    except Exception as e:
        app.logger.error(f"Error loading Google Sheet: {str(e)}")
        return []

def make_call(phone_data):
    with app.app_context():
        try:
            phone_number = phone_data.get('phone_number')
            if not phone_number:
                return None
            name = phone_data.get('name', '')
            message = phone_data.get('message', '')

            # Encode parameters to URL query safely
            from urllib.parse import urlencode
            params = urlencode({'name': name, 'message': message})
            call_url = f"{BASE_URL}/handle-call?{params}"
            status_cb_url = f"{BASE_URL}/call-status"

            call = client.calls.create(
                to=phone_number,
                from_=TWILIO_PHONE_NUMBER,
                url=call_url,
                record=True,
                recording_status_callback=f"{BASE_URL}/recording-callback",
                recording_status_callback_method='POST',
                status_callback=status_cb_url,
                status_callback_event=['completed']
            )
            calls_data[call.sid] = {
                'call_ssid': call.sid,
                'phone_number': phone_number,
                'name': name,
                'message': message,
                'status': 'initiated',
                'transcript': None,
                'recording_url': None,
                'gather_response': None  # new field for speech input during call
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
    name = request.args.get('name', '')
    message = request.args.get('message', '')

    greeting = "Hi" if not name else f"Hi, is this {name}?"
    response.say(f"{greeting}, My name is Samanta from DoWell Research.", voice='alice')

    if message:
        response.say(message, voice='alice')

    # Gather user speech response
    gather = Gather(input='speech', timeout=5, action=url_for('gather_response'), method='POST')
    gather.say("Please say Yes, No, or Call back later.", voice='alice')
    response.append(gather)

    # If no input, say goodbye
    response.say("We did not receive a response. Thank you for your time.", voice='alice')
    response.hangup()

    return Response(str(response), mimetype='text/xml')

@app.route('/gather-response', methods=['POST'])
def gather_response():
    response = VoiceResponse()

    speech_result = request.values.get('SpeechResult', '').lower()
    call_sid = request.values.get('CallSid')

    # Save the gathered speech response text
    if call_sid in calls_data:
        calls_data[call_sid]['gather_response'] = speech_result

    # Match user response and reply accordingly
    if 'yes' in speech_result:
        response.say("Thank you for the response. We will send you an invite shortly.", voice='alice')
    elif 'no' in speech_result:
        response.say("Thank you for the response. We appreciate your time.", voice='alice')
    elif 'call back later' in speech_result or 'i will call back' in speech_result:
        response.say("Thank you for the response. We will call you back at another time", voice='alice')
    else:
        response.say("Sorry, I did not understand your response.", voice='alice')

    response.hangup()
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
            return jsonify({'Error': 'No valid phone numbers found in CSV. Please ensure your csv includes a "phone_number" column with numeric values.'}), 400

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

@app.route('/cancel-calls', methods=['POST'])
def cancel_calls():
    """Cancel ongoing calls"""
    try:
        data = request.json
        call_sids = data.get('call_sids', [])
        
        if not call_sids:
            return jsonify({'error': 'No call SIDs provided'}), 400
        
        canceled_count = 0
        failed_cancellations = []
        
        for call_sid in call_sids:
            try:
                # Check if the call exists in our data
                if call_sid in calls_data:
                    # Only try to cancel calls that are not already completed
                    if calls_data[call_sid]['status'] not in ['completed', 'failed', 'busy', 'no-answer', 'canceled']:
                        # Cancel the call via Twilio API
                        call = client.calls(call_sid).update(status='canceled')
                        
                        # Update our local data
                        calls_data[call_sid]['status'] = 'canceled'
                        canceled_count += 1
            except Exception as e:
                app.logger.error(f"Error canceling call {call_sid}: {str(e)}")
                failed_cancellations.append(call_sid)
        
        return jsonify({
            'message': f'Successfully canceled {canceled_count} calls',
            'canceled_count': canceled_count,
            'failed_cancellations': failed_cancellations
        })
    
    except Exception as e:
        app.logger.error(f"Error in cancel_calls: {str(e)}")
        return jsonify({'error': f'Failed to cancel calls: {str(e)}'}), 500

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
    os.makedirs('templates', exist_ok=True)
    port = int(os.environ.get("PORT", 10000))  # fallback to 10000 for local dev
    app.run(host="0.0.0.0", port=port, debug=True)

