from flask import Flask, render_template, Response, jsonify, request, redirect, url_for, session
import cv2
import mediapipe as mp
import numpy as np
import tensorflow as tf
from tensorflow import keras
import os
from collections import deque
import json
from deep_translator import GoogleTranslator
import threading
import time
import random
import re
otp_storage = {}

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f)

app = Flask(__name__)
app.secret_key = "secret123"
USERS_FILE = "users.json"

class SignLanguageTranslator:
    def __init__(self, model_path, labels_path):
        """Initialize the translator"""
        # Load model
        self.model = keras.models.load_model(model_path)
        self.labels = np.load(labels_path)
        
        # Initialize MediaPipe
        self.mp_hands = mp.solutions.hands
        self.mp_drawing = mp.solutions.drawing_utils
        self.hands = self.mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        
        # Prediction smoothing
        self.prediction_buffer = deque(maxlen=10)
        self.confidence_threshold = 0.70
        
        # Gesture stability
        self.last_gesture = None
        self.gesture_stable_count = 0
        self.stability_threshold = 15
        
        # Sentence
        self.sentence = []
    
    def extract_landmarks(self, hand_landmarks_list):
        """Extract hand landmarks - uses first detected hand for compatibility"""
        if not hand_landmarks_list:
            return None
        
        # Use first hand (for backward compatibility with existing models)
        hand_landmarks = hand_landmarks_list[0]
        landmarks = []
        for landmark in hand_landmarks.landmark:
            landmarks.extend([landmark.x, landmark.y, landmark.z])
        return np.array(landmarks).reshape(1, -1)
    
    def predict_gesture(self, landmarks):
        """Predict gesture from landmarks"""
        prediction = self.model.predict(landmarks, verbose=0)[0]
        predicted_class = np.argmax(prediction)
        confidence = prediction[predicted_class]
        
        if confidence > self.confidence_threshold:
            self.prediction_buffer.append((predicted_class, confidence))
        
        if len(self.prediction_buffer) > 0:
            classes = [pred[0] for pred in self.prediction_buffer]
            most_common = max(set(classes), key=classes.count)
            avg_confidence = np.mean([pred[1] for pred in self.prediction_buffer 
                                     if pred[0] == most_common])
            
            # Get all predictions for display
            all_preds = []
            for i, label in enumerate(self.labels):
                all_preds.append({
                    'label': label,
                    'confidence': float(prediction[i])
                })
            
            return self.labels[most_common], float(avg_confidence), all_preds
        
        return None, 0.0, []
    
    def check_gesture_stability(self, gesture):
        """Check if gesture is stable enough to add to sentence"""
        if gesture == self.last_gesture:
            self.gesture_stable_count += 1
        else:
            self.gesture_stable_count = 0
            self.last_gesture = gesture
        
        if self.gesture_stable_count == self.stability_threshold:
            self.gesture_stable_count = 0
            return True
        return False
    
    def reset_buffer(self):
        """Reset prediction buffer"""
        self.gesture_stable_count = 0
        self.last_gesture = None
        self.prediction_buffer.clear()
    
    def process_frame(self, frame):
        """Process a single frame and return results"""
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.hands.process(rgb_frame)
        
        gesture = None
        confidence = 0.0
        all_predictions = []
        hand_detected = False
        
        if results.multi_hand_landmarks:
            hand_detected = True
            # Draw ALL detected hands
            for hand_landmarks in results.multi_hand_landmarks:
                self.mp_drawing.draw_landmarks(
                    frame,
                    hand_landmarks,
                    self.mp_hands.HAND_CONNECTIONS,
                    self.mp_drawing.DrawingSpec(color=(0, 255, 0), thickness=2, circle_radius=3),
                    self.mp_drawing.DrawingSpec(color=(255, 255, 255), thickness=2)
                )
            
            # Predict using first hand (for compatibility)
            landmarks = self.extract_landmarks(results.multi_hand_landmarks)
            if landmarks is not None:
                gesture, confidence, all_predictions = self.predict_gesture(landmarks)
                
                # Check for auto-add
                if gesture and self.check_gesture_stability(gesture):
                    self.sentence.append(gesture)
        else:
            self.reset_buffer()
        
        return frame, gesture, confidence, all_predictions, hand_detected

# Load model
def load_model():
    """Load the trained model"""
    model_dir = "models"
    
    if not os.path.exists(model_dir):
        return None
    
    model_files = [f for f in os.listdir(model_dir) if f.endswith('.h5')]
    
    if not model_files:
        return None
    
    # Use the most recent model
    model_files.sort(reverse=True)
    model_file = os.path.join(model_dir, model_files[0])
    labels_file = model_file.replace('.h5', '_labels.npy')
    
    if not os.path.exists(labels_file):
        return None
    
    try:
        translator = SignLanguageTranslator(model_file, labels_file)
        return translator
    except Exception as e:
        print(f"Error loading model: {e}")
        return None

# Initialize translator
translator = load_model()
camera = None
camera_active = False
camera_lock = threading.Lock()

# ==================== ROUTES ====================


@app.route('/register', methods=['GET','POST'])
def register():

    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        phone = request.form['phone']
        password = request.form['password']
        question = request.form['question']
        answer = request.form['answer'].lower()

        users = load_users()

        # Username validation
        if username in users:
            return "Username already exists"

        if not username.isalnum() or len(username) < 3:
            return "Invalid username"

        # Email validation
        email_pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
        if not re.match(email_pattern, email):
            return "Invalid email"

        # Phone validation
        if len(phone) != 10 or not phone.isdigit():
            return "Invalid phone number"

        # Strong password validation
        password_pattern = r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&]).{6,}$'
        if not re.match(password_pattern, password):
            return "Password must contain uppercase, lowercase, number & special character"

        users[username] = {
            "email": email,
            "phone": phone,
            "password": password,
            "question": question,
            "answer": answer
        }

        save_users(users)
        return redirect(url_for('login'))

    return render_template('register.html')


# ================= LOGIN =================
@app.route('/login', methods=['GET','POST'])
def login():

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        users = load_users()

        if username in users and users[username]['password'] == password:
            session['user'] = username
            return redirect(url_for('welcome'))

        return "Invalid username or password"

    return render_template('login.html')


# ================= FORGOT =================
@app.route('/forgot', methods=['GET','POST'])
def forgot():

    if request.method == 'POST':
        username = request.form['username']
        answer = request.form['answer'].lower()

        users = load_users()

        if username in users:

            if users[username]['answer'] == answer:
                session['reset_user'] = username
                return redirect(url_for('reset'))

            else:
                return "Wrong answer"

        return "User not found"

    return render_template('forgot.html')


# ================= RESET =================
@app.route('/reset', methods=['GET','POST'])
def reset():

    if 'reset_user' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        new_password = request.form['new_password']

        # Strong password validation
        password_pattern = r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&]).{6,}$'
        if not re.match(password_pattern, new_password):
            return "Weak password"

        users = load_users()
        username = session['reset_user']

        users[username]['password'] = new_password
        save_users(users)

        session.pop('reset_user')

        return redirect(url_for('login'))

    return render_template('reset.html')
@app.route('/')
def home():
    return redirect(url_for('register'))

@app.route('/welcome')
def welcome():

    if 'user' not in session:
        return redirect(url_for('login'))
    """Render the welcome page"""
    if translator is None:
        return "Model not found! Please train a model first.", 500
    return render_template('welcome.html')
@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/camera')
def camera_route():

    if 'user' not in session:
        return redirect(url_for('login'))
    """Render the camera detection page"""
    if translator is None:
        return "Model not found! Please train a model first.", 500
    return render_template('camera.html', gestures=translator.labels.tolist())

@app.route('/translation')
def translation():

    if 'user' not in session:
        return redirect(url_for('login'))
    """Render the translation page"""
    if translator is None:
        return "Model not found! Please train a model first.", 500
    return render_template('translation.html')

@app.route('/thankyou')
def thankyou():
    """Render the thank you page"""
    if translator is None:
        return "Model not found! Please train a model first.", 500
    return render_template('thankyou.html')

@app.route('/original')
def original():
    """Render the original single-page interface (kept for reference)"""
    if translator is None:
        return "Model not found! Please train a model first.", 500
    return render_template('index.html', gestures=translator.labels.tolist())

# ==================== API ENDPOINTS ====================

def generate_frames():
    """Generate frames for video streaming with frame skipping for performance"""
    global camera, camera_active
    
    frame_count = 0
    
    try:
        while camera_active:
            with camera_lock:
                if camera is None or not camera_active:
                    break
                    
                success, frame = camera.read()
                if not success:
                    print("Failed to read frame from camera")
                    break
            
            frame = cv2.flip(frame, 1)
            
            # Frame skipping: process every 2nd frame for better performance
            frame_count += 1
            if frame_count % 2 == 0:
                # Process frame
                try:
                    processed_frame, gesture, confidence, all_preds, hand_detected = \
                        translator.process_frame(frame)
                except Exception as e:
                    print(f"Error processing frame: {e}")
                    processed_frame = frame
            else:
                # Skip processing, just show frame
                processed_frame = frame
            
            # Encode frame
            try:
                ret, buffer = cv2.imencode('.jpg', processed_frame)
                frame_bytes = buffer.tobytes()
                
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            except Exception as e:
                print(f"Error encoding frame: {e}")
                break
    except Exception as e:
        print(f"Error in generate_frames: {e}")
    finally:
        print("Video feed stopped")

@app.route('/video_feed')
def video_feed():
    """Video streaming route"""
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/start_camera', methods=['POST'])
def start_camera():
    """Start the camera"""
    global camera, camera_active
    
    with camera_lock:
        try:
            if not camera_active:
                print("Attempting to start camera...")
                camera = cv2.VideoCapture(0)
                
                if not camera.isOpened():
                    print("Failed to open camera at index 0, trying index 1...")
                    camera = cv2.VideoCapture(1)
                
                if not camera.isOpened():
                    print("Camera could not be opened!")
                    camera = None
                    return jsonify({'status': 'error', 'message': 'Camera could not be opened. Please check if camera is available.'})
                
                camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                camera.set(cv2.CAP_PROP_FPS, 15)
                camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                
                # Test read
                ret, _ = camera.read()
                if not ret:
                    print("Camera opened but cannot read frames!")
                    camera.release()
                    camera = None
                    return jsonify({'status': 'error', 'message': 'Camera opened but cannot read frames.'})
                
                camera_active = True
                print("Camera started successfully!")
                return jsonify({'status': 'success', 'message': 'Camera started'})
            
            return jsonify({'status': 'error', 'message': 'Camera already active'})
        except Exception as e:
            print(f"Error starting camera: {e}")
            camera = None
            camera_active = False
            return jsonify({'status': 'error', 'message': f'Error starting camera: {str(e)}'})

@app.route('/stop_camera', methods=['POST'])
def stop_camera():
    """Stop the camera"""
    global camera, camera_active
    
    print("Stop camera requested...")
    
    # First, stop the video feed loop
    camera_active = False
    
    # Wait for video feed to stop
    time.sleep(0.5)
    
    # Now safely release the camera
    with camera_lock:
        if camera is not None:
            try:
                print("Releasing camera...")
                camera.release()
                print("Camera released!")
            except Exception as e:
                print(f"Warning during camera release: {e}")
            finally:
                camera = None
    
    print("Camera stopped successfully!")
    return jsonify({'status': 'success', 'message': 'Camera stopped'})

@app.route('/get_prediction', methods=['GET'])
def get_prediction():
    """Get current prediction"""
    global camera, camera_active
    
    try:
        if not camera_active:
            return jsonify({
                'gesture': None,
                'confidence': 0,
                'all_predictions': [],
                'hand_detected': False,
                'sentence': translator.sentence
            })
        
        with camera_lock:
            if camera is None:
                return jsonify({
                    'gesture': None,
                    'confidence': 0,
                    'all_predictions': [],
                    'hand_detected': False,
                    'sentence': translator.sentence
                })
            
            success, frame = camera.read()
            if not success:
                return jsonify({
                    'gesture': None,
                    'confidence': 0,
                    'all_predictions': [],
                    'hand_detected': False,
                    'sentence': translator.sentence
                })
        
        frame = cv2.flip(frame, 1)
        _, gesture, confidence, all_preds, hand_detected = translator.process_frame(frame)
        
        return jsonify({
            'gesture': gesture,
            'confidence': confidence,
            'all_predictions': all_preds,
            'hand_detected': hand_detected,
            'sentence': translator.sentence
        })
    except Exception as e:
        print(f"Error in get_prediction: {e}")
        return jsonify({
            'gesture': None,
            'confidence': 0,
            'all_predictions': [],
            'hand_detected': False,
            'sentence': translator.sentence
        })

@app.route('/add_to_sentence', methods=['POST'])
def add_to_sentence():
    """Manually add current gesture to sentence"""
    data = request.json
    gesture = data.get('gesture')
    
    if gesture:
        translator.sentence.append(gesture)
        return jsonify({'status': 'success', 'sentence': translator.sentence})
    
    return jsonify({'status': 'error', 'message': 'No gesture provided'})

@app.route('/clear_sentence', methods=['POST'])
def clear_sentence():
    """Clear the sentence"""
    translator.sentence.clear()
    return jsonify({'status': 'success', 'sentence': []})

@app.route('/get_sentence', methods=['GET'])
def get_sentence():
    """Get current sentence"""
    return jsonify({'sentence': translator.sentence})

@app.route('/get_gestures', methods=['GET'])
def get_gestures():
    """Get all available gestures"""
    return jsonify({'gestures': translator.labels.tolist()})

@app.route('/translate_text', methods=['POST'])
def translate_text():
    """Translate text to selected language"""
    try:
        data = request.json
        text = data.get('text')
        target_lang = data.get('target_lang', 'en')
        
        if not text:
            return jsonify({'status': 'error', 'message': 'No text provided'})
        
        # If target is English, no translation needed
        if target_lang == 'en':
            return jsonify({
                'status': 'success',
                'original': text,
                'translated': text,
                'target_lang': target_lang
            })
        
        # Translate using deep-translator
        translated = GoogleTranslator(source='en', target=target_lang).translate(text)
        
        return jsonify({
            'status': 'success',
            'original': text,
            'translated': translated,
            'target_lang': target_lang
        })
    except Exception as e:
        print(f"Translation error: {e}")
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/get_languages', methods=['GET'])
def get_languages():
    """Get available languages for translation"""
    languages = {
        'en': 'English',
        'hi': 'Hindi',
        'es': 'Spanish',
        'fr': 'French',
        'de': 'German',
        'it': 'Italian',
        'pt': 'Portuguese',
        'ru': 'Russian',
        'ja': 'Japanese',
        'ko': 'Korean',
        'zh-cn': 'Chinese (Simplified)',
        'ar': 'Arabic',
        'ta': 'Tamil',
        'te': 'Telugu',
        'kn': 'Kannada',
        'ml': 'Malayalam',
        'bn': 'Bengali',
        'mr': 'Marathi',
        'gu': 'Gujarati',
        'ur': 'Urdu'
    }
    return jsonify({'languages': languages})


    if request.method == 'POST':
        username = request.form['username']
        answer = request.form['answer'].lower()

        users = load_users()

        if username in users:

            if users[username]['answer'] == answer:
                session['reset_user'] = username
                return redirect(url_for('reset'))

            else:
                return "Wrong answer"

        return "User not found"

    return render_template('forgot.html')


    







if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
