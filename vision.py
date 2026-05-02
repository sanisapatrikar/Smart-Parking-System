import cv2
import pytesseract
import re
import time

# >>> UPDATE THIS IP ADDRESS TO YOUR NEW HOTSPOT IP <<<
_CAMERA_SOURCE = "http://100.64.23.17:8080/video" 

def capture_frame():
    print("Opening camera stream...")
    cap = cv2.VideoCapture(_CAMERA_SOURCE)
    time.sleep(0.5) # Let auto-exposure settle
    ret, frame = cap.read()
    cap.release()
    
    if ret and frame is not None:
        return frame
    print("Camera failed to capture frame.")
    return None

def preprocess_frame(frame):
    # Same great preprocessing from your original code
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.bilateralFilter(gray, 11, 17, 17)
    return cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)

def scan_plate():
    frame = capture_frame()
    if frame is None: 
        return None

    processed = preprocess_frame(frame)
    
    # Run REAL OCR using Tesseract (--psm 8 optimizes for a single line of text like a license plate)
    print("Running Tesseract OCR...")
    raw_text = pytesseract.image_to_string(processed, config='--psm 8')
    
    # Clean up the output (remove spaces, special characters, make uppercase)
    clean_text = re.sub(r"[^A-Z0-9]", "", raw_text.upper())
    
    print(f"OCR Result: {clean_text}")
    
    # If we got at least 4 valid characters, accept it as a plate
    if len(clean_text) >= 4:
        return clean_text
        
    return None
