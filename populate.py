import os
import django
import random
import requests
from django.core.files.base import ContentFile
from faker import Faker
from django.utils import timezone

# --------------------------------
# Setup Django environment
# --------------------------------
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ghprocument.settings")
django.setup()

# --------------------------------
# Import models
# --------------------------------
from cms.models import User, Portfolio, PortfolioImages, Blog

fake = Faker()

# --------------------------------
# 1. Create a test user
# --------------------------------
print("Creating user...")

try:
    user = User.objects.create_user(
        username="demo_user",
        password="DemoPass123",
        phone=fake.phone_number()
    )

    # Real profile image
    dp_url = "https://picsum.photos/seed/userdp/400/400"
    dp_resp = requests.get(dp_url)
    user.dp.save("profile.jpg", ContentFile(dp_resp.content))
    user.save()

    print("User created.")
    print("Profile picture URL:", user.dp.url)

except Exception as e:
    print("User already exists or error:", e)

# --------------------------------
# 2. Create PortfolioImages
# --------------------------------
print("\nCreating portfolio images...")
portfolio_images = []

for i in range(20):
    url = f"https://picsum.photos/seed/portfolio{i}/600/400"
    resp = requests.get(url)

    img = PortfolioImages.objects.create()
    img.image.save(f"portfolio_{i}.jpg", ContentFile(resp.content))
    img.save()
    portfolio_images.append(img)

    print(f"Portfolio image {i} URL:", img.image.url)

print(f"Created {len(portfolio_images)} portfolio images.")

# --------------------------------
# 3. Create Portfolio items
# --------------------------------
print("\nCreating portfolios...")

categories = [choice[0] for choice in Portfolio.Categories.choices]

for i in range(7):
    # Thumbnail image
    thumb_url = f"https://picsum.photos/seed/thumb{i}/500/300"
    thumb_resp = requests.get(thumb_url)

    date_completed = timezone.make_aware(
        fake.date_time_between(start_date='-1y', end_date='now')
    )

    portfolio = Portfolio.objects.create(
        title=fake.sentence(nb_words=5),
        client_name=fake.company(),
        description=fake.paragraph(nb_sentences=7),
        category=random.choice(categories),
        is_featured=random.choice([True, False]),
        date_completed=date_completed
    )

    # Save thumbnail via CloudinaryField
    portfolio.thumbnail.save(f"thumbnail_{i}.jpg", ContentFile(thumb_resp.content))
    portfolio.save()

    # Attach 2–6 random portfolio images
    portfolio.images.set(random.sample(portfolio_images, k=random.randint(2, 6)))

    print(f"Portfolio '{portfolio.title}' created with thumbnail URL:", portfolio.thumbnail.url)
    print("Associated image URLs:")
    for img in portfolio.images.all():
        print("-", img.image.url)

print("Portfolios created successfully.")

# --------------------------------
# 4. Create Blogs
# --------------------------------
print("\nCreating blogs...")

for i in range(10):
    url = f"https://picsum.photos/seed/blog{i}/700/450"
    resp = requests.get(url)

    created_at = timezone.make_aware(
        fake.date_time_between(start_date='-2y', end_date='now')
    )
    updated_at = timezone.make_aware(
        fake.date_time_between(start_date='-1y', end_date='now')
    )

    blog = Blog.objects.create(
        title=fake.sentence(nb_words=6),
        content=fake.paragraph(nb_sentences=30),
        excerpt=fake.paragraph(nb_sentences=3),
        is_published=True,
        views_count=random.randint(0, 1500),
        created_at=created_at,
        updated_at=updated_at,
    )

    # Save featured image via CloudinaryField
    blog.featured_image.save(f"blog_{i}.jpg", ContentFile(resp.content))
    blog.save()

    print(f"Blog '{blog.title}' created with featured image URL:", blog.featured_image.url)

print("\n🎉 Database populated with real images and Cloudinary URLs printed for verification!")
