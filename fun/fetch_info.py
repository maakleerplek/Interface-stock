import requests
import json
import os

def fetch_random_info():
    """Fetches a random fact from the Useless Facts API."""
    url = "https://uselessfacts.jsph.pl/random.json?language=en"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        fact = data.get("text", "No fact found.")
        
        # Save to a local file for the display script to read
        with open("random_fact.json", "w") as f:
            json.dump({"fact": fact}, f)
            
        print(f"Successfully fetched and saved fact: {fact[:50]}...")
        return fact
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

if __name__ == "__main__":
    fetch_random_info()
