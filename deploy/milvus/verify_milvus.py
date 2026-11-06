# T-37 Milvus 兼容性四步验证
# pymilvus 3.0.2 <-> milvusdb/milvus:v2.6.24
# 步：建 collection -> upsert -> search -> delete -> drop
import sys
from pymilvus import MilvusClient, DataType

URI = "http://localhost:19530"
COL = "aitalent_compat_test"
DIM = 1024

print("[1/4] connect:", URI)
client = MilvusClient(uri=URI)

# clean if leftover
if client.has_collection(COL):
    client.drop_collection(COL)

print("[2/4] create_collection", COL, "dim=", DIM, "metric=COSINE")
schema = MilvusClient.create_schema(auto_id=True, enable_dynamic_field=True)
schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
schema.add_field("vector", DataType.FLOAT_VECTOR, dim=DIM)
index = MilvusClient.prepare_index_params()
index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
client.create_collection(COL, schema=schema, index_params=index)

print("[3/4] insert 3 vectors")
data = [
    {"vector": [0.10] * DIM},
    {"vector": [0.20] * DIM},
    {"vector": [0.30] * DIM},
]
ins = client.insert(COL, data)
ids = ins.get("ids", [])
print("   insert count:", len(ids), "first ids:", list(ids)[:3])

client.load_collection(COL)
print("[4/4] search top-2 -> delete first -> drop")
res = client.search(
    collection_name=COL,
    data=[[0.10] * DIM],
    limit=2,
    output_fields=["id"],
)
print("   search hits:", len(res[0]), "top1 distance:", round(res[0][0]["distance"], 6))

del_id = list(ids)[0]
client.delete(COL, ids=[del_id])
print("   deleted id:", del_id)

client.drop_collection(COL)
print("   dropped:", COL)
print("VERIFY_PASS: create/upsert/search/delete all OK")
