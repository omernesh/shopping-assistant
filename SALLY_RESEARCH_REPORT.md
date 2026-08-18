# HeySally (סאלי) - Comprehensive Research Report

**Date:** February 25, 2026
**Source:** https://www.heysally.co.il/
**Report Type:** Feature Analysis & Claude Skill Development

---

## 1. What is Sally?

**Sally (סאלי)** is an Israeli shopping assistant chatbot that helps users manage their grocery shopping lists. Originally launched as "Groceroo" in late 2023, it was renamed to "Sally" in early 2025. The service went viral in February 2025, growing from ~25,000 to over 200,000 users within days after being shared on Facebook groups.

**Creator:** Eitan Schiller (עידן שילר), an independent software developer from Tel Aviv.

**Platform:** WhatsApp Bot + Web Application (React-based)

---

## 2. Core Features

### 2.1 Shopping List Management

| Feature | Description |
|---------|-------------|
| **Add Items** | Simply type any item in WhatsApp and it gets added to the list |
| **Remove Items** | Mark items as "bought" (קניתי) or delete them (מחק) |
| **View List** | Request current list with "תראה" or "?" command |
| **Quantities** | Support for adding amounts (e.g., "3 קוטג'") |
| **Notes** | Add notes to items |

### 2.2 Automatic Categorization

- Items are automatically sorted by supermarket department/category
- Categories include: Dairy, Produce, Frozen, Bakery, etc.
- Saves time in the store by organizing the shopping route

### 2.3 Shared Lists

- Share list with family members or anyone
- Real-time synchronization across all devices
- Multiple people can add/edit simultaneously

### 2.4 Price Scanner (סורק המחירים)

- Scan product prices at local supermarkets
- Compare prices between stores
- Find current promotions and deals

### 2.5 Expense Tracking

- Automatic tracking of purchases
- Purchase history stored
- Monthly/yearly spending summaries
- Query: "כמה עלה לי?" (How much did I spend?)

### 2.6 Deal Calculator

- Calculate final price of sales/promotions
- Compare sale price vs. regular price
- Determine if deals are worthwhile

### 2.7 Customizable Category Order

- Users can customize the order of categories
- Optimize store route based on personal preference

---

## 3. How It Works

### 3.1 WhatsApp Bot Commands

```
# Basic Usage
Just type item names → Added to list automatically

# Commands
תראה / ?     → View current list
קניתי        → Mark items as purchased
מחק          → Delete items from list
עוד פקודות   → Show more commands
```

### 3.2 Web Interface

- **Instant List Creation:** Type items on website → generates organized list
- **Checklist Mode:** Check off items while shopping in store
- **Paste Function:** Paste existing list for instant organization
- No login/installation required

### 3.3 Sync Mechanism

- WhatsApp ↔ Web App synchronization
- Real-time sync across all household devices
- Works on any device with WhatsApp or browser

---

## 4. Technical Architecture

### 4.1 Technology Stack

| Component | Technology |
|-----------|------------|
| WhatsApp Integration | WhatsApp Business API |
| Frontend Web App | React |
| Backend | Custom (self-hosted) |
| Data Storage | Secure servers (self-managed) |

### 4.2 Key Design Decisions

- **No installation required** - Uses WhatsApp everyone already has
- **Hybrid approach** - Not just bot, not just app, but combination
- **Privacy-focused** - No data sold to third parties
- **WhatsApp-first** - Leverages existing user habits

---

## 5. Pricing Model

### Free Version
- Basic shopping list management
- Automatic categorization
- Shared lists
- Web interface access

### Paid Version (₪X/month)
- Multiple parallel lists (produce, butcher, pharmacy)
- Permanent/recurring lists
- Quantities and notes
- Split list between two shoppers
- Extended features

---

## 6. Media Coverage & Traction

| Source | Headline | Date |
|--------|----------|------|
| Ynet | בוט רשימת הקניות שסחף את המדינה | Feb 13, 2025 |
| Kan (Channel 11) | איך סאלי הבוט יעזור לכם בקניות בסופר | Feb 16, 2025 |
| Channel 13 | לנהל את רשימת הקניות לסופר - בלחיצת כפתור | Feb 16, 2025 |
| GeekTime | בוט ישראלי חדש יוצר לכם רשימת קניות חכמה בוואטסאפ | Oct 19, 2024 |
| Channel 2000 | 120 אלף איש כבר משתמשים ב"סאלי" | Feb 19, 2025 |

---

## 7. Future Roadmap (Based on Interviews)

The creator mentioned these planned features:

1. **Voice Messages** - Convert voice notes to list items
2. **Photo to List** - Take photo of paper note → auto-organize list
3. **Automatic Weekly List** - Generate weekly list based on purchase history
4. **Smart Suggestions** - Suggest complementary items based on history
5. **Direct Purchase** - One-click transfer to supermarket websites for ordering
6. **Missing Item Detection** - Mark items as "out of stock" → auto-add to next week's list

---

## 8. User Pain Points Solved

| Pain Point | Sally's Solution |
|------------|------------------|
| Paper notes on fridge get lost | Digital list accessible anywhere |
| WhatsApp "list with myself" is disorganized | Auto-categorized structured list |
| Can't share list with spouse | Real-time shared list |
| Forgot what you bought last time | Purchase history tracking |
| Don't know if deals are worthwhile | Deal calculator |
| Waste time back-and-forth in store | Customizable category order |
| Can't track spending | Automatic expense tracking |

---

## 9. For Claude Skill Development

To create a "Sally-like" shopping assistant, consider implementing:

### Core Capabilities
1. **Natural Language Item Parsing** - Extract items from casual text
2. **Category Classification** - ML/categorize items by supermarket section
3. **List Synchronization** - Multi-user real-time sync
4. **Checklist UI** - Interactive check-off interface
5. **Expense Calculation** - Track and summarize spending
6. **Deal Analysis** - Calculate if promotions are worthwhile

### Integration Points
- WhatsApp API for messaging
- Web app (React) for visual checklist
- Database for persistence and history
- **Price Comparison API** (see Section 11)

### Key Differentiators from Generic Lists
- Hebrew language support
- Israeli supermarket categories
- WhatsApp-native experience
- Simplicity (no app install)

---

## 11. Price Comparison API - Implementation

### Overview
To implement the price scanner feature (סורק המחירים), we analyzed Israeli price comparison services:

| Service | Status | API |
|---------|--------|-----|
| **chp.co.il** | ✅ Working | Internal API available |
| **pricez.co.il** | ❌ Blocked | Requires browser automation |

### CHP.co.il API Details

**Base URL:** `https://chp.co.il`

**Price Search Endpoint:**
```
GET https://chp.co.il/main_page/compare_results
```

**Parameters:**
| Parameter | Description | Example |
|-----------|-------------|---------|
| `shopping_address` | City name | יבנה |
| `shopping_address_city_id` | City ID | 2660 |
| `shopping_address_street_id` | Street ID | 9000 |
| `product_name_or_barcode` | Product name | חלב |
| `num_results` | Results count | 20 |

### Python Scraper

**Location:** `chp_price_scraper.py`

**Features:**
- Type-safe with dataclasses
- Retry logic with exponential backoff
- Logging support
- CLI with argparse
- Multiple city support
- **Barcode search support**
- **Hebrew encoding handling** (proper UTF-8 decoding from raw bytes)

**Usage:**
```bash
# Default city (י�בנה)
python chp_price_scraper.py "מלפפונים"

# Custom city
python chp_price_scraper.py "חלב" "תל אביב"

# Search by barcode
python chp_price_scraper.py "7290121290548"

# With flags
python chp_price_scraper.py -c יבנה "ביצים"

# List available cities
python chp_price_scraper.py --list-cities
```

**Barcode Search Example:**
```
$ python chp_price_scraper.py "7290121290548"

🔍 Product: וינסטון קומפקט ברי
   Barcode: 7290121290548

🏪 Physical Stores in יבנה:
--------------------------------------------------
   ₪39.00 - קשת טעמים (אשדוד)
   ₪39.00 - יוחננוף (גן יבנה)
   ₪40.00 - yellow (רשף יבנה)
   ...
```

**Technical Notes:**
- The CHP API returns Hebrew text encoded as UTF-8 bytes
- Must use `response.content` (raw bytes) and decode as UTF-8 to get correct Hebrew
- Using `response.text` corrupts Hebrew characters
- Product name extracted from `displayed_product_name_and_contents` hidden field

**Sample Output:**
```
🔍 Product: מלפפונים, מחיר לפי משקל

🏪 Physical Stores in יבנה:
--------------------------------------------------
   ₪3.90 - רמי לוי (יבנה)
   ₪3.90 - מחסני השוק בשבילך (יבנה)
   ₪6.90 - שופרסל דיל (יבנה)
   ₪7.90 - ויקטורי פלוס (יבנה)
```

### Available Cities
- יבנה (default)
- תל אביב
- ירושלים
- חיפה
- באר שבע
- רמת גן
- פתח תקווה
- ראשון לציון
- נתניה
- אשדוד
- רמת השרון
- הרצליה
- כפר סבא
- נהריה
- אילת
- בת ים

### Legal Notes
- Prices are provided by the supermarkets directly
- Review terms of service for commercial use
- Attribution may be required
- Personal/non-commercial use typically allowed

---

## 10. References

- Website: https://www.heysally.co.il/
- WhatsApp: https://wa.me/message/FCQVXLASJXGDH1
- Contact: contact@heysally.co.il

---

*Report generated for Claude Skill development purposes*
