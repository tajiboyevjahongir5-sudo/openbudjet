import asyncio, aiohttp, json

async def main():
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
    async with aiohttp.ClientSession() as s:
        async with s.get('https://openbudget.uz/api/v1/regions', headers=headers) as r:
            reg_data = await r.json()
            regions = reg_data['regions']
            
        final_regions = []
        final_districts = {}
        for reg in regions:
            reg_id = reg['id']
            final_regions.append({'id': reg_id, 'name': reg['title'], 'short_name': reg['title']})
            async with s.get(f'https://openbudget.uz/api/v1/districts?regionId={reg_id}', headers=headers) as r2:
                dist_data = await r2.json()
                dists = []
                for d in dist_data['districts']:
                    dists.append({'id': d['id'], 'name': d['title']})
                final_districts[reg_id] = dists
        
        with open('utils/regions.py', 'w', encoding='utf-8') as f:
            f.write('REGIONS = ' + json.dumps(final_regions, ensure_ascii=False, indent=4) + '\n\n')
            
            f.write('DISTRICTS = {\n')
            for k, v in final_districts.items():
                f.write(f'    {k}: {json.dumps(v, ensure_ascii=False)},\n')
            f.write('}\n\n')
            
            f.write('''def get_region_name(region_id: int) -> str:
    for reg in REGIONS:
        if reg["id"] == region_id:
            return reg["name"]
    return "Noma'lum viloyat"

def get_district_name(region_id: int, district_id: int) -> str:
    districts = DISTRICTS.get(region_id, [])
    for d in districts:
        if d["id"] == district_id:
            return d["name"]
    return "Noma'lum tuman"
''')
        print('Updated utils/regions.py successfully')
asyncio.run(main())
