import json
import os
import time
from datetime import datetime

class BillingSystem:
    def __init__(self, db_file="database.json"):
        self.db_file = db_file
        self.base_rate = 50  # Rs per hour
        self.peak_rate = 75  # Rs per hour
        self.peak_start = 17 # 5:00 PM
        self.peak_end = 22   # 10:00 PM
        
        # Initialize an empty database if it doesn't exist
        if not os.path.exists(self.db_file):
            with open(self.db_file, 'w') as f:
                json.dump({}, f)

    def _load_db(self):
        with open(self.db_file, 'r') as f:
            return json.load(f)

    def _save_db(self, data):
        with open(self.db_file, 'w') as f:
            json.dump(data, f, indent=4)

    def log_entry(self, plate_number, slot_number, custom_time=None):
        db = self._load_db()
        
        # Use provided time (for testing) or actual current time
        entry_time = custom_time if custom_time else time.time()
        
        db[str(slot_number)] = {
            "plate": plate_number,
            "entry_time": entry_time
        }
        self._save_db(db)
        print(f"[ENTRY LOGGED] Plate: {plate_number} -> Slot: {slot_number}")

    def log_exit(self, slot_number, current_time=None):
        db = self._load_db()
        slot_key = str(slot_number)
        
        if slot_key not in db:
            print(f"[ERROR] No vehicle found in Slot {slot_number}")
            return

        record = db[slot_key]
        entry_time = record["entry_time"]
        plate = record["plate"]
        
        exit_time = current_time if current_time else time.time()
        
        # Calculate duration in hours
        duration_seconds = exit_time - entry_time
        duration_hours = duration_seconds / 3600.0
        
        # Minimum charge is 1 hour for testing simplicity
        if duration_hours < 1:
            duration_hours = 1.0
            
        bill_amount = self._calculate_bill(duration_hours, exit_time)
        
        # Remove from database
        del db[slot_key]
        self._save_db(db)
        
        print(f"\n--- RECEIPT ---")
        print(f"Vehicle: {plate}")
        print(f"Duration: {round(duration_hours, 2)} hours")
        print(f"Total Due: Rs. {round(bill_amount, 2)}")
        print(f"----------------\n")

    def _calculate_bill(self, duration_hours, exit_time):
        """Calculates bill with dynamic peak hour pricing."""
        current_hour = datetime.fromtimestamp(exit_time).hour
        
        # Check if exit happens during peak hours
        if self.peak_start <= current_hour < self.peak_end:
            print("[INFO] Peak pricing applied.")
            return duration_hours * self.peak_rate
        else:
            return duration_hours * self.base_rate

# ==========================================
# TEST BLOCK (Runs only if executed directly)
# ==========================================
if __name__ == "__main__":
    print("--- STARTING SOFTWARE-ONLY BILLING TEST ---\n")
    
    billing = BillingSystem("test_db.json")
    
    # Simulate a car entering
    test_plate = "MH04-AB-1234"
    test_slot = 2
    
    # Time-travel trick: We pretend the car entered 2.5 hours ago
    two_and_a_half_hours_ago = time.time() - (2.5 * 3600)
    
    billing.log_entry(test_plate, test_slot, custom_time=two_and_a_half_hours_ago)
    
    # Read the database to prove it saved
    print("Current Database State:", billing._load_db())
    
    # Simulate the car leaving right now
    print("\nSimulating car exit...")
    billing.log_exit(test_slot)
    
    print("Final Database State:", billing._load_db())
    print("Test Complete.")
