# Israeli Price Comparison APIs Research

## Overview
Analysis of two Israeli price comparison websites: **chp.co.il** and **pricez.co.il**

---

## 1. CHP.co.il API

### Base URL
```
https://chp.co.il
```

### Autocomplete Endpoints

#### Shopping Address Autocomplete
```
GET https://chp.co.il/autocompletion/shopping_address?term={search_term}
```
**Parameters:**
- `term`: City/street name to search
- `from`: Pagination offset (default 0)
- `u`: Random identifier

**Example:**
```
https://chp.co.il/autocompletion/shopping_address?term=%D7%99%D7%91%D7%A0%D7%94&from=0&u=0.21671132824219919
```

#### Product Autocomplete
```
GET https://chp.co.il/autocompletion/product_extended?term={product_name}&shopping_address={address}&shopping_address_city_id={city_id}&shopping_address_street_id={street_id}
```
**Parameters:**
- `term`: Product name (Hebrew URL encoded)
- `shopping_address`: City name (Hebrew URL encoded)
- `shopping_address_city_id`: City ID (e.g., 2660 for יבנה)
- `shopping_address_street_id`: Street ID (e.g., 9000 for default)
- `from`: Pagination offset
- `u`: Random identifier

**Example:**
```
https://chp.co.il/autocompletion/product_extended?term=%D7%97%D7%9C%D7%91&from=0&u=0.21671132824219919&shopping_address=%D7%99%D7%91%D7%A0%D7%94+&shopping_address_city_id=2660&shopping_address_street_id=9000
```

### Price Comparison Endpoint
```
GET https://chp.co.il/main_page/compare_results
```

**Parameters:**
| Parameter | Description | Example |
|-----------|-------------|---------|
| `shopping_address` | City name (Hebrew) | יבנה |
| `shopping_address_city_id` | City ID | 2660 |
| `shopping_address_street_id` | Street ID | 9000 |
| `product_name_or_barcode` | Product name or barcode | חלב |
| `product_barcode` | Barcode number (0 if name) | 0 |
| `from` | Pagination offset | 0 |
| `num_results` | Number of results | 20 |

**Example Full URL:**
```
https://chp.co.il/main_page/compare_results?shopping_address=%D7%99%D7%91%D7%A0%D7%94+&shopping_address_street_id=9000&shopping_address_city_id=2660&product_name_or_barcode=%D7%97%D7%9C%D7%91&product_barcode=0&from=0&num_results=20
```

### Response Format
The API returns HTML page with:
- Product info (name, brand, barcode)
- Store prices in table format
- Online store prices
- Price range (percentage difference)
- Store names, addresses, and current prices

### Sample Response Data (חלב דל לקטוז 2%)
| Store | Address | Price |
|-------|---------|-------|
| רמי לוי | יבנה | ₪7.20 |
| ויקטורי פלוס | יבנה | ₪7.90 |
| שופרסל דיל | יבנה | ₪8.30 |
| Carrefour | יבנה | ₪8.90 |
| מיני סופר אלונית | יבנה | ₪9.20 |
| מחסני להב | יבנה | ₪9.60 |
| שופרסל אקספרס | יבנה | ₪9.90 |
| Yellow | יבנה | ₪10.00 |

---

## 2. Pricez.co.il API

### Status: BLOCKED
The pricez API at `https://prices.pricez.co.il/` returns "Access denied" for direct API calls.

### Alternative: Web Scraping
Pricez appears to require JavaScript rendering and blocks direct API access. Use Playwright/Puppeteer for scraping.

### Known Endpoints (from network analysis)
- Main site: `https://www.pricez.co.il/`
- Category browsing: `https://www.pricez.co.il/Category/{category_id}`
- Search (推测): `https://www.pricez.co.il/Search?q={query}`

---

## 3. Implementation Recommendations

### For CHP Integration

1. **Get City ID First**
   - Use autocomplete endpoint to get city_id
   - Cache city IDs for frequently used cities

2. **Product Search Flow**
   ```
   1. User enters city → call shopping_address autocomplete
   2. User enters product → call product_extended autocomplete
   3. User selects product → call compare_results
   ```

3. **API Call Example (Python)**
   ```python
   import requests
   from urllib.parse import quote

   def search_product(product_name: str, city: str, city_id: int):
       # Step 1: Get prices
       url = "https://chp.co.il/main_page/compare_results"
       params = {
           "shopping_address": f"{city}+",
           "shopping_address_city_id": city_id,
           "shopping_address_street_id": 9000,
           "product_name_or_barcode": product_name,
           "product_barcode": 0,
           "from": 0,
           "num_results": 20
       }
       response = requests.get(url, params=params)
       return response.text  # Parse HTML for prices
   ```

4. **Response Parsing**
   - Parse HTML response for `<table>` elements
   - Extract store name, address, price
   - Handle both physical stores and online stores sections

### Rate Limiting
- No explicit rate limits found
- Add delays between requests (1-2 seconds recommended)
- Cache results for common searches

---

## 4. Data Fields Available

| Field | CHP | Pricez |
|-------|-----|--------|
| Product Name | ✅ | ✅ |
| Barcode | ✅ | ✅ |
| Brand | ✅ | ✅ |
| Store Name | ✅ | ✅ |
| Store Address | ✅ | ✅ |
| Price | ✅ | ✅ |
| Discount/Sale | ✅ | ✅ |
| Online Stores | ✅ | ✅ |

---

## 5. Legal Considerations

- Both services source prices from supermarkets directly
- Terms of service should be reviewed for commercial use
- Attribution may be required
- Personal/non-commercial use typically allowed
