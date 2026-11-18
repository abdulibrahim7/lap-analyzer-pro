import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
import main

ROOT = Path(__file__).resolve().parents[1]


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                        base_url="http://127.0.0.1")
        self.addAsyncCleanup(self.client.aclose)
        self.csv = (ROOT / 'sample_telemetry.csv').read_text()

    async def analysis(self):
        response = await self.client.post('/analyze', json={'csv_text': self.csv})
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def test_health_and_ui(self):
        self.assertTrue((await self.client.get('/health')).json()['ok'])
        page = await self.client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Coaching Feedback', page.text)
        self.assertNotIn('<th>Tags</th>', page.text)

    async def test_analyze(self):
        result = await self.analysis()
        self.assertEqual(result['total_time_loss'], 1.53)
        self.assertEqual(result['row_count'], 5)
        self.assertEqual([r['corner'] for r in result['top_corners']], ['T1','T9','T3','T5','T7'])

    async def test_multipart(self):
        response = await self.client.post('/analyze', files={'file':('sample.csv', self.csv, 'text/csv')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), await self.analysis())

    async def test_aliases_and_notes(self):
        response = await self.client.post('/analyze', json={'csv_text':'\ufeffTurn;Time Loss;Tags\nT2;0,42;early braking\nT1;0,12;exit'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['top_corners'][0]['notes'], 'early braking')
        self.assertEqual(response.json()['total_time_loss'], .54)

    async def test_bad_csv(self):
        for text in ('', 'Speed,Throttle\n1,2', 'Corner,TimeLoss\nT1,NaN', 'Corner,TimeLoss\nT1,-1', 'Corner,TimeLoss\nT1,broken'):
            with self.subTest(text=text):
                self.assertEqual((await self.client.post('/analyze', json={'csv_text':text})).status_code, 400)

    async def test_quoted_semicolon_header_and_strict_csv(self):
        accepted = await self.client.post('/analyze', json={'csv_text': '"Corner";"TimeLoss"\n"T1";"0,42"'})
        self.assertEqual(accepted.status_code, 200)
        for text in ('Corner,TimeLoss\nT1,0,42', 'Corner,TimeLoss\n"T1,0.42'):
            with self.subTest(text=text):
                response = await self.client.post('/analyze', json={'csv_text': text})
                self.assertEqual(response.status_code, 400)

    async def test_cross_origin_forms_and_rebinding_host_rejected(self):
        files = {'file': ('sample.csv', self.csv, 'text/csv')}
        response = await self.client.post('/analyze', files=files, headers={'Origin': 'https://example.invalid'})
        self.assertEqual(response.status_code, 403)
        response = await self.client.get('/health', headers={'Host': 'example.invalid'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual((await self.client.post(
            '/analyze', files=files, headers={'Origin': 'http://127.0.0.1'}
        )).status_code, 200)

    async def test_upload_limits_before_parsing(self):
        with patch('main.MAX_CSV', 16):
            result = await self.client.post('/analyze', json={'csv_text': self.csv})
            self.assertEqual(result.status_code, 400)
            with patch('main.parse_csv_flexible') as parse:
                result = await self.client.post('/analyze', files={'file': ('large.csv', b'x' * 65_000)})
                self.assertEqual(result.status_code, 413)
                parse.assert_not_called()
        async def chunks():
            yield b'x' * 32_000
            yield b'x' * 32_001

        result = await self.client.post('/coach', content=chunks(),
                                  headers={'Content-Type': 'application/json'})
        self.assertEqual(result.status_code, 413)

    async def test_fallback_skips_zero_loss_when_other_corners_have_loss(self):
        text = main._rule_based_english({
            'top_corners': [{'corner': 'T1', 'time_loss': .5}, {'corner': 'Zero', 'time_loss': 0}],
            'total_time_loss': .5,
        })
        self.assertNotIn('Zero', text)


    async def test_coach_fallback(self):
        with patch('main._llm_english', new=AsyncMock(side_effect=RuntimeError('test failure'))):
            result = (await self.client.post('/coach', json=await self.analysis())).json()
        self.assertEqual(result['source'], 'rule-based')
        self.assertIn('Drills:', result['coaching_text'])
        self.assertIn('not an LLM', result['warning'])

    async def test_coach_llm_contract(self):
        with patch('main._llm_english', new=AsyncMock(return_value='Focus on T1. Compare entry speed.')) as model:
            response = await self.client.post('/coach', json=await self.analysis())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['source'], 'llm')
        self.assertEqual(response.json()['coaching_text'], 'Focus on T1. Compare entry speed.')
        model.assert_awaited_once()

    async def test_invalid_coach_input(self):
        self.assertEqual((await self.client.post('/coach', json={})).status_code, 422)


class ProviderTest(unittest.IsolatedAsyncioTestCase):
    def test_provider_url_requires_https_for_remote_and_rejects_userinfo(self):
        self.assertEqual(main.provider_url('http://127.0.0.1:11434/'),
                         ('http://127.0.0.1:11434/v1', True))
        self.assertEqual(main.provider_url('https://api.openai.com/v1'),
                         ('https://api.openai.com/v1', False))
        for url in ('http://example.invalid/v1', 'https://user@example.invalid/v1',
                    'https://example.invalid/v1?value=1', 'https://example.invalid:99999/v1'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                main.provider_url(url)

    async def test_remote_provider_bearer_and_no_redirect(self):
        real_client = httpx.AsyncClient

        def handler(request):
            self.assertEqual(str(request.url), 'https://api.openai.com/v1/chat/completions')
            self.assertEqual(request.headers['Authorization'], 'Bearer placeholder-test-value')
            return httpx.Response(307, headers={'Location': 'https://example.invalid/v1'})

        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        with patch.multiple(main, LOCAL=False, BASE_URL='https://api.openai.com/v1',
                            API_KEY='placeholder-test-value'), patch('main.httpx.AsyncClient', side_effect=client):
            with self.assertRaises(httpx.HTTPStatusError):
                await main._llm_english({'top_corners': [{'corner': 'T1', 'time_loss': .42}]})

    async def test_local_request_and_few_shot(self):
        import json
        real_client = httpx.AsyncClient
        def handler(request):
            self.assertEqual(str(request.url), 'http://127.0.0.1:11434/v1/chat/completions')
            self.assertNotIn('Authorization', request.headers)
            body = json.loads(request.content)
            self.assertEqual([m['role'] for m in body['messages']], ['system','user','assistant','user'])
            self.assertIn('professional race engineer', body['messages'][0]['content'])
            self.assertIn('T1', body['messages'][-1]['content'])
            return httpx.Response(200, json={'choices':[{'message':{'content':'Compare the reference lap.'}}]})
        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        with patch.multiple(main, LOCAL=True, BASE_URL='http://127.0.0.1:11434/v1', API_KEY='fake-test-key'), patch('main.httpx.AsyncClient', side_effect=client):
            text = await main._llm_english({'top_corners':[{'corner':'T1','time_loss':.42}]})
        self.assertEqual(text, 'Compare the reference lap.')


if __name__ == '__main__':
    unittest.main()
