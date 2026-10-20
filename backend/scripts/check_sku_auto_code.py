"""新建可省略编码、并发唯一、CSV仍要求编码；仅一次性隔离库可运行。"""
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from _test_support import require_api_base, require_isolated_db
from _db_helper import db


def main():
    require_isolated_db()
    base = require_api_base()
    token = None
    product_id = None

    def call(method, path, body=None, *, raw=None, content_type=None):
        payload = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": content_type or "application/json"}
        if token: headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(base + path, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def ok(method, path, body=None):
        status, result = call(method, path, body)
        assert status == 200 and result.get("code") == 0, (status, result)
        return result["data"]

    token = ok("POST", "/auth/login", {"username": "admin", "password": "admin123"})["access_token"]
    marker = "SKU-AUTO-TEST-" + uuid4().hex[:8]
    try:
        product_id = ok("POST", "/products", {"name": marker})["id"]

        def create(index):
            body = {"name": f"{marker}-{index}", "specification": f"600×{400 + index}"}
            if index % 4 == 0: body["sku_code"] = None
            if index % 4 == 1: body["sku_code"] = ""
            if index % 4 == 2: body["sku_code"] = "  "
            path = f"/products/{product_id}/skus" if index % 2 else "/skus"
            if path == "/skus": body["product_id"] = product_id
            row = ok("POST", path, body)
            assert row["name"] == body["name"] and row["specification"] == body["specification"]
            return row

        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(create, range(16)))
        codes = {row["sku_code"] for row in rows}
        assert len(codes) == 16 and all(code.strip() and len(code) <= 64 for code in codes)
        print("PASS 16 concurrent creations across both APIs have unique automatic codes and retain name/spec")
        manual = marker + "-COMPANY"
        row = ok("POST", f"/products/{product_id}/skus", {"sku_code": manual, "name": "已有公司编码"})
        changed = ok("PATCH", f"/skus/{row['id']}", {"name": "修改名称"})
        assert changed["sku_code"] == manual
        status, result = call("POST", "/skus", {"product_id": product_id, "sku_code": manual})
        assert status == 409 and result.get("code") != 0
        print("PASS manual code preserved through edit, duplicate still rejected")
        for invalid in ({"name": "x" * 201}, {"specification": "x" * 201}, {"weight": -1}):
            status, result = call("POST", f"/products/{product_id}/skus", invalid)
            assert status in (400, 422) and result.get("code") != 0
        print("PASS automatic code does not bypass name/spec/numeric validation")
        boundary = "sku-code-isolated-upload"
        csv = "SKU编码,产品名称,规格名称,规格\n," + marker + ",导入名称,600×400\n"
        multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sku.csv"\r\n'
                     f'Content-Type: text/csv\r\n\r\n{csv}\r\n--{boundary}--\r\n').encode()
        status, result = call("POST", "/skus/import", raw=multipart,
                              content_type=f"multipart/form-data; boundary={boundary}")
        assert status == 200 and result.get("code") == 0, (status, result)
        assert result["data"]["failed"] and "SKU编码不能为空" in str(result["data"]["failed"])
        all_rows = ok("GET", f"/products/{product_id}/skus")
        assert len(all_rows) == 17
        print("PASS CSV still requires code and does not create a row for blank code")
    finally:
        if product_id:
            # 编号来自本脚本创建的记录；护栏已确认是一次性隔离库。
            db(f"delete from audit_logs where business_type='sku' and business_id in (select id from skus where product_id={int(product_id)})")
            db(f"delete from skus where product_id={int(product_id)}")
            db(f"delete from audit_logs where business_type='product' and business_id={int(product_id)}")
            db(f"delete from products where id={int(product_id)}")


if __name__ == "__main__":
    main()
