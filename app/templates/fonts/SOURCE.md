# 동봉 폰트 출처 (BE-07, D-06)

서버 PDF 생성에 쓰는 OFL 오픈 폰트. 라이선스 원문은 같은 폴더의 `LICENSE-OFL.txt`(SIL Open Font License 1.1, Reserved Font Name "Pretendard").

| 항목 | 값 |
|---|---|
| 폰트 | Pretendard (정적 TTF, `public/static/alternative/`) |
| 버전 | v1.3.9 (릴리스 2023-11-05) |
| 출처 | https://github.com/orioncactus/pretendard/releases/tag/v1.3.9 · 파일 `Pretendard-1.3.9.zip` |
| zip sha256 | 04be351a74d6bf7d60c480a3087e51d185485d35a52023142af1df19eb8c428a |
| 내려받은 날 | 2026-09-26 |

| 파일 | 바이트 | sha256 |
|---|---|---|
| Pretendard-Regular.ttf | 2,725,828 | 6d0af5258997aec7354a6e340fc2325ba321c410ca48b3af858c8c3d6e92a324 |
| Pretendard-Bold.ttf | 2,661,752 | c16b88c670d23e83fa1170c954cbc4822d3b8dad3c3cde15d798a94b43d97985 |
| LICENSE-OFL.txt | 4,419 | b04538c9abec39a3db75108cf0af0fd9c77032fe8aa2cf38345b4d250e98e38e |

- TTF(정적)를 고른 이유: 브라우저와 reportlab 둘 다 읽을 수 있다(OTF/CFF는 reportlab 미지원).
- 두 굵기(Regular 400 · Bold 700)만 동봉한다. 폰트 파일을 바꾸면 `app/services/layout_checks.py`의 `TEMPLATE_VERSION`을 올려야 한다(가드 테스트 `tests/test_be07.py`).
- DOCX에는 글꼴 이름만 지정한다. 이번 구현(python-docx)에서는 폰트 임베딩을 지원하지 않으므로 받는 사람 환경에 Pretendard가 없으면 다른 글꼴로 대체될 수 있다.
