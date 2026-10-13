import json, urllib.request
def get(u):
    req = urllib.request.Request("https://www.tesourodireto.com.br/"+u, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print(u, r.status, r.headers.get("content-type"))
        return json.load(r)
inv = get("o/rentabilidade/investir"); res = get("o/rentabilidade/resgatar")
for nome, d in (("investir", inv), ("resgatar", res)):
    print("==", nome, type(d).__name__, list(d) if isinstance(d, dict) else len(d))
    for k, v in d.items():
        print("  chave", k, type(v).__name__, len(v) if hasattr(v, "__len__") else v)
        if isinstance(v, list):
            for it in v:
                print("   ", it.get("treasuryBondName"), "|", it.get("maturityDate"), "|", it.get("investmentProfitabilityIndexerName"), "|", it.get("redemptionProfitabilityFeeIndexerName"), "|", it.get("unitaryInvestmentValue"), "|", it.get("unitaryRedemptionValue"), "|", it.get("lastMarketPricingDate"))
            if v: print("   CAMPOS", sorted(v[0]))
ni = {(i["treasuryBondName"], i["maturityDate"]) for v in inv.values() if isinstance(v, list) for i in v}
nr = {(i["treasuryBondName"], i["maturityDate"]) for v in res.values() if isinstance(v, list) for i in v}
print("so em investir:", ni - nr)
json.dump(res, open("resgatar.json","w"), ensure_ascii=False)
json.dump(inv, open("investir.json","w"), ensure_ascii=False)
