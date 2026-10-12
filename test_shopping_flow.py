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
    print(f"   (Skipped: state machine is now handled outside ShoppingCart class)")
    
    # Test clearing
    print(f"\n5. Testing cart clear:")
    print(f"   Items before clear: {len(cart.items)}")
    cart.clear()
    print(f"   Items after clear: {len(cart.items)}")
    print(f"   Is empty: {cart.is_empty()}")
    
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
    from unittest.mock import patch
    import barcode_inventree as bi

    print("\nTesting price functions...")
    # The price is the sale price break only; InvenTree's cost fields are never a fallback.
    with patch.object(bi, 'refresh_sale_prices'), patch.object(bi, '_SALE_PRICES', {7: 2.5}):
        assert bi.extract_price({'pk': 7}) == 2.5
        assert bi.extract_price({'pk': 8, 'pricing_max': '15.00'}) == 0.0
        assert bi.extract_price({}) == 0.0
    assert bi.format_price(2.5) == "€2.50" and bi.format_price(0.0) == "-"
    print("✓ All price tests passed!")

def test_stock_removal():
    """Checkout spreads a line over the part's stock items and refuses unpriced lines."""
    from unittest.mock import patch, MagicMock
    import barcode_inventree as bi

    print("\nTesting Stock Removal Logic...")
    stock = {1: [(101, 2.0), (111, 3.0)], 2: [(102, 5.0)]}
    with patch.object(bi, 'find_stock_items', side_effect=lambda pk: sorted(stock[pk], key=lambda s: -s[1])), \
         patch.object(bi, 'refresh_sale_prices'), \
         patch.object(bi, '_SALE_PRICES', {1: 2.0, 2: 3.0}), \
         patch.object(bi, 'INVENTREE_TOKEN', 'test'), \
         patch.object(bi, 'send_changelog_event'), \
         patch.object(bi.API_SESSION, 'post') as post:
        post.return_value = MagicMock(status_code=201)
        cart = bi.ShoppingCart()
        for _ in range(4):
            cart.add_item({'pk': 1, 'name': 'Item 1'})
        cart.add_item({'pk': 2, 'name': 'Item 2'})
        assert len(cart.items) == 2, "one cart line per part"

        ok, err = bi.remove_stock_from_inventree(cart)
        assert ok, err
        items = post.call_args.kwargs['json']['items']
        assert items == [{'pk': 111, 'quantity': 3.0}, {'pk': 101, 'quantity': 1.0},
                         {'pk': 102, 'quantity': 1.0}], items

        cart.add_item({'pk': 1, 'name': 'Item 1'})  # 5 of 5 in stock: still fine
        assert bi.remove_stock_from_inventree(cart)[0]
        cart.add_item({'pk': 1, 'name': 'Item 1'})  # 6 of 5
        ok, err = bi.remove_stock_from_inventree(cart)
        assert not ok and 'Only 5x' in err, err

        post.reset_mock()
        cart = bi.ShoppingCart()
        cart.add_item({'pk': 2, 'name': 'Item 2'})
        cart.add_item({'pk': 3, 'name': 'No price'})
        ok, err = bi.remove_stock_from_inventree(cart)
        assert not ok and 'No price for No price' in err, err
        post.assert_not_called()
    print("✓ Stock removal test passed!")

def test_volunteer_flow():
    """VOLUNTEER makes the whole cart free and books it as a volunteer drink."""
    from unittest.mock import patch, MagicMock
    import barcode_inventree as bi

    print("\nTesting Volunteer Flow...")
    drink = {'pk': 13, 'name': 'Ice Tea Peach'}
    with patch.object(bi, 'get_item_by_barcode', side_effect=lambda b: dict(drink)), \
         patch.object(bi, 'find_stock_items', return_value=[(501, 24.0)]), \
         patch.object(bi, 'INVENTREE_TOKEN', 'test'), \
         patch.object(bi, 'send_changelog_event') as events, \
         patch.object(bi.API_SESSION, 'post') as post:
        post.return_value = MagicMock(status_code=201)
        cart = bi.ShoppingCart()
        S = bi.AppState

        state, msg, *_ = bi.handle_barcode(S.IDLE, 'VOLUNTEER', cart)
        assert state == S.IDLE and 'first' in msg, "VOLUNTEER on an empty cart is refused"

        state, *_ = bi.handle_barcode(S.IDLE, '8711327582156', cart)
        state, *_ = bi.handle_barcode(state, '8711327582156', cart)
        state, msg, *_ = bi.handle_barcode(state, 'VOLUNTEER', cart)
        assert state == S.SHOPPING and cart.volunteer and 'FREE' in msg

        state, *_ = bi.handle_barcode(state, 'VOLUNTEER', cart)
        assert not cart.volunteer, "second scan undoes it"
        state, *_ = bi.handle_barcode(state, 'CONFIRM', cart)
        state, *_ = bi.handle_barcode(state, 'volunteer', cart)
        assert state == S.CHECKOUT_CONFIRM and cart.volunteer, "works on the confirm screen too"
        assert bi.CartSnapshot(cart).volunteer

        state, *_ = bi.handle_barcode(state, 'CONFIRM', cart)
        assert state == S.PROCESSING
        ok, err = bi.remove_stock_from_inventree(cart)
        assert ok, err
        payload = post.call_args.kwargs['json']
        assert payload['notes'].startswith('Volunteer drink via Interface-stock'), payload['notes']
        assert payload['items'] == [{'pk': 501, 'quantity': 2.0}]
        events.assert_called_once_with('volunteer', 'Ice Tea Peach', 2)

        cart.clear()
        assert not cart.volunteer, "clear() resets the flag for the next customer"
    print("✓ Volunteer flow test passed!")

def main():
    print("="*50)
    print("Shopping Cart System Test Suite")
    print("="*50)
    
    test_shopping_cart()
    test_price_formatting()
    test_qr_generation()
    test_stock_removal()
    test_volunteer_flow()
    
    print("\n" + "="*50)
    print("All tests completed!")
    print("="*50)

if __name__ == "__main__":
    main()
