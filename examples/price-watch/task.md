Open http://localhost:8765/ with browser_navigate and read the name, price and stock of every
product in the catalog with browser_snapshot (product filter: {product}). Then open
http://localhost:8765/previous.json the same way (browser_navigate, then browser_snapshot) to
see the previous values. If any price changed or any product went out of stock, post a short
alert to #alerts with the alerts tool, listing the changed products and their old and new
prices; otherwise do not post anything. Use only the browser and alerts tools.
