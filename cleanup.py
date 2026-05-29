import os
import glob

# photos 폴더의 모든 jpg 파일 삭제
photo_dir = r'c:\Users\태민\Desktop\rebloom\photos'
for file in glob.glob(os.path.join(photo_dir, '*.jpg')):
    try:
        os.remove(file)
        print(f"Deleted: {file}")
    except Exception as e:
        print(f"Error: {e}")

print("Cleanup complete!")
