import sys
import json
import asyncio
import argparse
import datetime

from . import client


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--hostname', type=str, required=True)
    parser.add_argument('--tlsa', type=str, nargs='+', required=True)
    parser.add_argument('--password-file', type=str, required=True)
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--output-directory', type=str, required=True)
    args = parser.parse_args()

    enable_logging = False
    if args.trace:
        logging.basicConfig(level=logging.DEBUG)
        enable_logging = True

    http_api_username_password = client.get_username_password(args.password_file)

    enable_logging = False
    if args.trace:
        logging.basicConfig(level=logging.DEBUG)
        enable_logging = True

    async with client.Client(hostname=args.hostname, tlsa=args.tlsa, http_api_username_password=http_api_username_password, enable_logging=enable_logging) as client_inst:
        async def get_image(time, fn):
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            start = now_utc.astimezone()

            # Note: This will return 1280x960 images when the time parameter is set
            params = {
                'width': '1920',
                'height': '1440',
                'source': 'internal',
            }
            if time != 0:
                params['time'] = str(time)

            response = await client_inst.api_request('GET', '/api/camera/snapshot', params=params)

            if response.status != 200:
                raise Exception(f'Got response {response}')

            ct = response.headers['Content-Type']

            if ct == 'application/json':
                data = json.loads(await response.read())

                if not data['success']:
                    code = data['error']['code']
                    description = data['error']['description']
                    param = data['error'].get('param')
                    raise Exception(f'While getting image got error {code!r}: {description!r} {param!r}')

                raise Exception('Got unexpected JSON response')

            if ct != 'image/jpeg':
                raise Exception(f'Got unknown content type {ct} from 2N')

            data = await response.read()

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            end = now_utc.astimezone()

            print(f'Got image time={time} from {start.isoformat()} took to {end - start}s, fn={fn!r}', flush=True)

            with open(fn, 'wb') as file:
                file.write(data)

        # for time in [-30, -25, -20, -15, -10, -5, 0]:
        for time in range(-30, 1):
            fn = args.output_directory + f'/img_m{-time:02}.jpeg'
            await get_image(time=time, fn=fn)

        for i in range(5):
            await asyncio.sleep(1)
            fn = args.output_directory + f'/img_p{i:02}.jpeg'
            await get_image(time=0, fn=fn)

# From api/camera/caps ['jpegResolution']:
# {
#   "width": 176,
#   "height": 144
# },
# {
#   "width": 320,
#   "height": 240
# },
# {
#   "width": 352,
#   "height": 288
# },
# {
#   "width": 640,
#   "height": 480
# },
# {
#   "width": 800,
#   "height": 600
# },
# {
#   "width": 1280,
#   "height": 960
# },
# {
#   "width": 160,
#   "height": 120
# },
# {
#   "width": 352,
#   "height": 272
# },
# {
#   "width": 480,
#   "height": 272
# },
# {
#   "width": 1024,
#   "height": 600
# },
# {
#   "width": 1280,
#   "height": 720
# },
# {
#   "width": 1920,
#   "height": 1080
# },
# {
#   "width": 1920,
#   "height": 1440
# },
# {
#   "width": 640,
#   "height": 360
# }

if __name__ == '__main__':
    asyncio.run(main())
