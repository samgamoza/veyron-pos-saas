-- E-Town marketplace MVP bootstrap schema (PostgreSQL).
-- Standalone for fresh databases. The Veyron Flask app uses a wider schema
-- (categories, units, guest order fields, etc.); see app/core/flask_app.py init_db
-- when sharing one database with the full POS.

-- Optional: start clean in a brand new DB only.
-- DROP TABLE IF EXISTS order_items CASCADE;
-- DROP TABLE IF EXISTS orders CASCADE;
-- DROP TABLE IF EXISTS customer_addresses CASCADE;
-- DROP TABLE IF EXISTS customers CASCADE;
-- DROP TABLE IF EXISTS product_variants CASCADE;
-- DROP TABLE IF EXISTS products CASCADE;
-- DROP TABLE IF EXISTS tenants CASCADE;

BEGIN;

-- =========================================================
-- Base/core tables required by the marketplace extension
-- =========================================================

CREATE TABLE IF NOT EXISTS tenants (
  id integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  name text NOT NULL,
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now(),

  -- marketplace/public fields
  public_business_name text,
  public_subtitle text,
  public_description text,
  public_address text,
  public_phone text,
  public_cover_image text,
  public_logo_image text,
  public_opening_hours text,
  storefront_visibility text NOT NULL DEFAULT 'hidden',
  marketplace_enabled integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS products (
  id integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  tenant_id integer NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name text NOT NULL,
  sku text,
  price numeric(12,2) NOT NULL DEFAULT 0,
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now(),

  -- marketplace fields
  is_public integer NOT NULL DEFAULT 0,
  marketplace_description text,
  marketplace_image_path text,
  marketplace_sort_order integer NOT NULL DEFAULT 0,
  marketplace_published_at text,
  slug text
);

CREATE INDEX IF NOT EXISTS products_tenant_id_idx ON products(tenant_id);
CREATE INDEX IF NOT EXISTS products_slug_idx ON products(slug);

CREATE TABLE IF NOT EXISTS product_variants (
  id integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  product_id integer NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  name text NOT NULL,
  price numeric(12,2),
  stock_qty integer NOT NULL DEFAULT 0,
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS product_variants_product_id_idx ON product_variants(product_id);

-- =========================================================
-- Marketplace/customer/order tables
-- =========================================================

CREATE TABLE IF NOT EXISTS customers (
  id integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  email text,
  phone text NOT NULL,
  full_name text NOT NULL,
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS customers_phone_idx ON customers(phone);

CREATE TABLE IF NOT EXISTS customer_addresses (
  id integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  customer_id integer NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  label text,
  province text NOT NULL,
  municipality text NOT NULL,
  barangay text NOT NULL,
  address_line text NOT NULL,
  landmark_notes text,
  is_primary integer NOT NULL DEFAULT 0,
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS customer_addresses_customer_id_idx
  ON customer_addresses(customer_id);

CREATE TABLE IF NOT EXISTS orders (
  id integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  customer_id integer NOT NULL REFERENCES customers(id),
  tenant_id integer NOT NULL REFERENCES tenants(id),
  source text NOT NULL DEFAULT 'marketplace',
  status text NOT NULL DEFAULT 'pending',
  payment_method text NOT NULL DEFAULT 'cash',
  subtotal numeric(12,2) NOT NULL DEFAULT 0,
  tax_amount numeric(12,2) NOT NULL DEFAULT 0,
  delivery_fee numeric(12,2) NOT NULL DEFAULT 0,
  total_amount numeric(12,2) NOT NULL DEFAULT 0,
  customer_notes text,
  merchant_notes text,
  address_id integer REFERENCES customer_addresses(id),
  requested_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now(),
  storefront_url text
);

CREATE INDEX IF NOT EXISTS orders_customer_id_idx ON orders(customer_id);
CREATE INDEX IF NOT EXISTS orders_tenant_id_idx ON orders(tenant_id);
CREATE INDEX IF NOT EXISTS orders_status_idx ON orders(status);

CREATE TABLE IF NOT EXISTS order_items (
  id integer PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  order_id integer NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id integer NOT NULL REFERENCES products(id),
  variant_id integer REFERENCES product_variants(id),
  tenant_id integer NOT NULL REFERENCES tenants(id),
  product_name text NOT NULL,
  quantity integer NOT NULL CHECK (quantity > 0),
  unit_price numeric(12,2) NOT NULL,
  line_total numeric(12,2) NOT NULL,
  created_at timestamp NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS order_items_order_id_idx ON order_items(order_id);

COMMIT;
