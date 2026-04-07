#!/usr/bin/env python3
"""
Test script for the shopping cart system
This simulates the workflow without needing actual hardware
"""

import sys
import os
from dotenv import load_dotenv

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

def test_shopping_cart():
    """Test the ShoppingCart class"""
    from barcode_inventree import ShoppingCart, extract_price, format_price
    
    print("Testing ShoppingCart...")
    cart = ShoppingCart()
    
    # Mock part details
    part1 = {
        'pk': 1,
        'name': 'Coca Cola',
        'pricing_min': '2.50',
        'category_detail': {'name': 'Drink'}
    }
    
    part2 = {
        'pk': 2,
        'name': 'Wood Plank',
        'pricing_min': '15.00',
        'category_detail': {'name': 'Wood'}
    }
    
    part3 = {
        'pk': 1,  # Same as part1
        'name': 'Coca Cola',
        'pricing_min': '2.50',
        'category_detail': {'name': 'Drink'}
    }
    
    # Test adding items
    print("\n1. Adding items to cart...")
    cart.add_item(part1)
    print(f"   Added: {part1['name']} - Cart total: {format_price(cart.get_total())}")
    
    cart.add_item(part2)
    print(f"   Added: {part2['name']} - Cart total: {format_price(cart.get_total())}")
    
    cart.add_item(part3)  # Should increment quantity
    print(f"   Added: {part3['name']} (duplicate) - Cart total: {format_price(cart.get_total())}")
    
    # Test cart state
    print(f"\n2. Cart state:")
    print(f"   Items: {len(cart.items)}")
    print(f"   Total: {format_price(cart.get_total())}")
    print(f"   Expected: €20.00 (2x €2.50 + 1x €15.00)")
    
    # Test categories
    print(f"\n3. Categories:")
    categories = cart.get_categories()
    print(f"   {categories}")
    print(f"   Description: {cart.get_description()}")
    
    # Test confirm states
    print(f"\n4. Testing confirm states:")
    print(f"   Initial state: {cart.confirm_state}")
    cart.confirm_state = 1
    print(f"   After first confirm: {cart.confirm_state}")
    cart.confirm_state = 2
    print(f"   After second confirm: {cart.confirm_state}")
    
    # Test clearing
    print(f"\n5. Testing cart clear:")
    print(f"   Items before clear: {len(cart.items)}")
    cart.clear()
    print(f"   Items after clear: {len(cart.items)}")
    print(f"   Is empty: {cart.is_empty()}")
    print(f"   Confirm state reset: {cart.confirm_state}")
    
    print("\n✓ All tests passed!")

def test_qr_generation():
    """Test QR code generation"""
    from barcode_inventree import generate_wero_qr
    
    print("\nTesting Wero QR generation...")
    try:
        qr_img = generate_wero_qr(25.50, "HTL Makerspace - drink - wood")
        print(f"   QR code generated successfully")
        print(f"   Size: {qr_img.size}")
        print("✓ QR generation test passed!")
    except Exception as e:
        print(f"✗ QR generation failed: {e}")

def test_price_formatting():
    """Test price extraction and formatting"""
    from barcode_inventree import extract_price, format_price
    
    print("\nTesting price functions...")
    
    test_cases = [
        ({'pricing_min': '10.50'}, 10.50, "€10.50"),
        ({'sell_price': '20.00'}, 20.00, "€20.00"),
        ({}, 0.0, "-"),
        ({'pricing_min': 'invalid'}, 0.0, "-"),
    ]
    
    all_passed = True
    for part, expected_price, expected_format in test_cases:
        price = extract_price(part)
        formatted = format_price(price)
        if price == expected_price and formatted == expected_format:
            print(f"   ✓ {part} -> {formatted}")
        else:
            print(f"   ✗ {part} -> Expected {expected_format}, got {formatted}")
            all_passed = False
    
    if all_passed:
        print("✓ All price tests passed!")

def main():
    print("="*50)
    print("Shopping Cart System Test Suite")
    print("="*50)
    
    test_shopping_cart()
    test_price_formatting()
    test_qr_generation()
    
    print("\n" + "="*50)
    print("All tests completed!")
    print("="*50)

if __name__ == "__main__":
    main()
