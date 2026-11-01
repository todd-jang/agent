-- Sample schema + data for the shared PostgreSQL DB (same data as the SQLite sample).
-- Replace with your real schema in production.

CREATE TABLE products  (id INTEGER PRIMARY KEY, name TEXT, category TEXT, price NUMERIC(10,2));
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, city TEXT);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id),
                     product_id INTEGER REFERENCES products(id), quantity INTEGER, order_date DATE);

INSERT INTO products VALUES
  (1, 'Laptop', 'Electronics', 1200.0),
  (2, 'Phone', 'Electronics', 800.0),
  (3, 'Headphones', 'Electronics', 150.0),
  (4, 'Desk', 'Furniture', 300.0),
  (5, 'Chair', 'Furniture', 180.0),
  (6, 'Notebook', 'Stationery', 5.0),
  (7, 'Pen Set', 'Stationery', 12.0),
  (8, 'Backpack', 'Accessories', 60.0);
INSERT INTO customers VALUES
  (1, 'Alice', 'Seoul'),
  (2, 'Bob', 'Busan'),
  (3, 'Chloe', 'Seoul'),
  (4, 'Daniel', 'Incheon'),
  (5, 'Eunji', 'Busan'),
  (6, 'Frank', 'Goyang');
INSERT INTO orders VALUES
  (1, 6, 2, 1, '2026-09-05'),
  (2, 2, 4, 2, '2026-08-14'),
  (3, 6, 2, 4, '2026-08-05'),
  (4, 1, 2, 2, '2026-08-30'),
  (5, 5, 1, 2, '2026-10-23'),
  (6, 6, 7, 2, '2026-09-27'),
  (7, 5, 5, 1, '2026-08-21'),
  (8, 6, 7, 3, '2026-09-05'),
  (9, 2, 4, 3, '2026-08-14'),
  (10, 1, 7, 1, '2026-09-15'),
  (11, 3, 5, 1, '2026-09-28'),
  (12, 5, 2, 4, '2026-08-11'),
  (13, 5, 5, 3, '2026-10-13'),
  (14, 2, 2, 1, '2026-10-24'),
  (15, 2, 5, 1, '2026-08-30'),
  (16, 1, 7, 3, '2026-09-28'),
  (17, 6, 6, 2, '2026-09-17'),
  (18, 3, 4, 3, '2026-10-22'),
  (19, 1, 3, 2, '2026-08-21'),
  (20, 4, 7, 3, '2026-10-21'),
  (21, 6, 4, 3, '2026-08-08'),
  (22, 2, 1, 3, '2026-09-21'),
  (23, 3, 2, 2, '2026-10-12'),
  (24, 6, 6, 2, '2026-10-23'),
  (25, 4, 7, 4, '2026-08-19'),
  (26, 3, 3, 2, '2026-10-11'),
  (27, 5, 5, 4, '2026-10-14'),
  (28, 4, 6, 2, '2026-08-18'),
  (29, 5, 8, 1, '2026-08-07'),
  (30, 1, 3, 2, '2026-09-24'),
  (31, 5, 2, 4, '2026-09-18'),
  (32, 5, 8, 3, '2026-10-10'),
  (33, 1, 2, 3, '2026-10-22'),
  (34, 3, 2, 3, '2026-09-25'),
  (35, 2, 8, 1, '2026-09-03'),
  (36, 5, 3, 1, '2026-10-20'),
  (37, 3, 4, 2, '2026-09-17'),
  (38, 2, 1, 3, '2026-10-02'),
  (39, 1, 2, 3, '2026-09-09'),
  (40, 2, 1, 2, '2026-10-12');
ANALYZE;
