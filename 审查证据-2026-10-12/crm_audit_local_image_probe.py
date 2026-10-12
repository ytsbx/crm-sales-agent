from PIL import Image
from pathlib import Path
from app.core.config import settings
from app.modules.quote.pdf import render_quote_pdf
path=Path('/tmp/crm_audit_private_image.png')
Image.new('RGB',(12,12),(121,15,47)).save(path)
try:
    customer=f'<img src="{path}" width="12" height="12"/>'
    result=render_quote_pdf({'quote_no':'AUDIT-LOCAL-IMAGE','customer_name':customer,'items':[], 'charges':[], 'summary':{'logistics_amount':0,'other_charge_amount':0}, 'total_amount':100})
    print('PDF_LOCAL_IMAGE', {'source_outside_file_root':not path.is_relative_to(Path(settings.file_root)), 'image_embedded':b'/Subtype /Image' in result, 'pdf_bytes':len(result)})
finally:
    path.unlink(missing_ok=True)
